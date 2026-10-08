"""Rotas HTTP da Trama. Toda a lógica de negócio fica nos módulos de domínio."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import custom_blocks, exchange
from .config import Settings
from .engine import Motor
from .errors import ApiError
from .models import BlockDraft, Connection, Flow
from .registry import Registro
from .sandbox import DockerExecutor
from .store import Store
from .tipos import exemplo_do_tipo, validar_json_puro
from .validation import analisar, verificar_conexao


@dataclass
class Servicos:
    settings: Settings
    store: Store
    registro: Registro
    executor: DockerExecutor
    motor: Motor


class Entrada(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjetoNovo(Entrada):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    flow: Flow | None = None


class ProjetoAtualizacao(Entrada):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    flow: Flow | None = None
    base_revision: int | None = None


class FluxoEntrada(Entrada):
    flow: Flow


class ConexaoEntrada(Entrada):
    flow: Flow
    connection: Connection


class ExportarEntrada(Entrada):
    name: str = Field(default="Fluxo", max_length=120)
    description: str = Field(default="", max_length=1000)
    flow: Flow


def _json_puro(v: Any) -> Any:
    try:
        validar_json_puro(v)
    except ValueError as e:
        raise ValueError(f"dados inválidos: {e}") from None
    return v


class ExecucaoEntrada(Entrada):
    flow: Flow | None = None  # se omitido, usa o fluxo salvo
    initial_data: dict[str, dict[str, Any]] | None = None

    _conferir = field_validator("initial_data")(_json_puro)


class CodigoEntrada(Entrada):
    code: str = Field(max_length=64 * 1024)


class RefBloco(Entrada):
    type: str
    version: int


class TesteBlocoEntrada(Entrada):
    draft: BlockDraft | None = None
    ref: RefBloco | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, Any] = Field(default_factory=dict)
    project_id: str | None = Field(default=None, max_length=64)

    _conferir = field_validator("params", "inputs")(_json_puro)


def criar_router(s: Servicos) -> APIRouter:
    r = APIRouter(prefix="/api")

    # ------------------------------------------------------------------ sistema
    @r.get("/sistema")
    def sistema() -> dict[str, Any]:
        lim = s.settings.limites
        return {
            "name": "Trama", "version": "0.1.0",
            "executor": s.executor.status().como_dict(),
            "limits": {"time_s": lim.tempo_s, "memory_mb": lim.memoria_mb, "cpus": lim.cpus,
                       "value_kb": lim.valor_max // 1024, "logs_kb": lim.logs_max // 1024,
                       "max_list_items": lim.itens_max_lista},
        }

    # ------------------------------------------------------------------- blocos
    @r.get("/blocos")
    def listar_blocos(todas_versoes: bool = False) -> list[dict[str, Any]]:
        return [t.model_dump() for t in s.registro.listar(todas_versoes)]

    @r.get("/blocos/{type_id}/versoes/{version}")
    def obter_bloco(type_id: str, version: int = Path(ge=1, le=1_000_000)) -> dict[str, Any]:
        t = s.registro.resolver(type_id, version)
        if t is None:
            raise ApiError(404, "bloco_nao_encontrado", "Esta versão do bloco não existe.")
        return t.model_dump()

    @r.post("/blocos", status_code=201)
    def criar_bloco(corpo: BlockDraft) -> dict[str, Any]:
        return custom_blocks.criar_bloco(s.store, s.executor, corpo)

    @r.put("/blocos/{type_id}", status_code=201)
    def nova_versao(type_id: str, corpo: BlockDraft) -> dict[str, Any]:
        return custom_blocks.nova_versao(s.store, s.registro, s.executor, type_id, corpo)

    @r.delete("/blocos/{type_id}", status_code=204, response_model=None)
    def excluir_bloco(type_id: str) -> None:
        custom_blocks.excluir_bloco(s.store, type_id)

    @r.post("/blocos/verificar")
    def verificar(corpo: CodigoEntrada) -> dict[str, Any]:
        return custom_blocks.verificar_codigo(s.executor, corpo.code)

    @r.post("/blocos/testar")
    def testar(corpo: TesteBlocoEntrada) -> dict[str, Any]:
        if (corpo.draft is None) == (corpo.ref is None):
            raise ApiError(422, "requisicao_invalida", "Informe o bloco a testar: um rascunho (draft) ou uma referência (ref).")
        if corpo.draft is not None:
            tipo = corpo.draft.para_tipo("custom.rascunho", 1)
        else:
            tipo = s.registro.resolver(corpo.ref.type, corpo.ref.version)  # type: ignore[union-attr]
            if tipo is None:
                raise ApiError(404, "bloco_nao_encontrado", "Este bloco não existe.")
        if corpo.project_id is not None and s.store.obter_projeto(corpo.project_id) is None:
            raise ApiError(404, "projeto_nao_encontrado", "Projeto não encontrado.")
        return s.motor.testar_bloco(tipo, corpo.params, corpo.inputs, corpo.project_id)

    @r.get("/blocos/{type_id}/exemplo")
    def exemplo(type_id: str, version: int | None = Query(default=None, ge=1, le=1_000_000)) -> dict[str, Any]:
        """Dados de exemplo para testar um bloco (um valor plausível por entrada/parâmetro)."""
        v = version or s.registro.ultima_versao(type_id)
        t = s.registro.resolver(type_id, v) if v else None
        if t is None:
            raise ApiError(404, "bloco_nao_encontrado", "Este bloco não existe.")
        return {"inputs": {p.id: exemplo_do_tipo(p.type) for p in t.inputs if p.required},
                "params": {p.id: p.default for p in t.params if p.default is not None}}

    # ----------------------------------------------------------------- projetos
    def _checar_estrutura(flow: Flow) -> None:
        analise = analisar(flow, s.registro.resolver)
        if analise.erros_de_estrutura:
            raise ApiError(422, "fluxo_invalido",
                           "O fluxo não foi salvo porque tem problemas na estrutura. O último fluxo salvo foi mantido.",
                           problemas=[i.model_dump() for i in analise.erros_de_estrutura])

    @r.get("/projetos")
    def listar_projetos() -> list[dict[str, Any]]:
        return s.store.listar_projetos()

    @r.post("/projetos", status_code=201)
    def criar_projeto(corpo: ProjetoNovo) -> dict[str, Any]:
        flow = corpo.flow or Flow()
        _checar_estrutura(flow)
        return s.store.criar_projeto(corpo.name, corpo.description, flow.model_dump())

    @r.post("/projetos/importar", status_code=201)
    def importar_projeto(corpo: dict[str, Any]) -> dict[str, Any]:
        res = exchange.importar(s.store, s.registro, corpo)
        projeto = s.store.criar_projeto(res["name"], res["description"], res["flow"])
        return {**projeto, "warnings": res["warnings"], "created_blocks": res["created_blocks"]}

    @r.post("/projetos/importar/validar")
    def validar_importacao(corpo: dict[str, Any]) -> dict[str, Any]:
        res = exchange.importar(s.store, s.registro, corpo, aplicar=False)
        return {"name": res["name"], "block_count": len(res["flow"]["blocks"]), "warnings": res["warnings"]}

    @r.get("/projetos/{pid}")
    def obter_projeto(pid: str) -> dict[str, Any]:
        p = s.store.obter_projeto(pid)
        if p is None:
            raise ApiError(404, "projeto_nao_encontrado", "Projeto não encontrado.")
        return p

    @r.put("/projetos/{pid}")
    def salvar_projeto(pid: str, corpo: ProjetoAtualizacao) -> dict[str, Any]:
        if corpo.flow is not None:
            _checar_estrutura(corpo.flow)  # se falhar, nada é gravado: o último fluxo salvo permanece
        return s.store.salvar_projeto(pid, corpo.name, corpo.description,
                                      corpo.flow.model_dump() if corpo.flow else None, corpo.base_revision)

    @r.delete("/projetos/{pid}", status_code=204, response_model=None)
    def excluir_projeto(pid: str) -> None:
        if not s.store.excluir_projeto(pid):
            raise ApiError(404, "projeto_nao_encontrado", "Projeto não encontrado.")

    # ------------------------------------------------------------------- fluxos
    @r.post("/fluxos/validar")
    def validar(corpo: FluxoEntrada) -> dict[str, Any]:
        a = analisar(corpo.flow, s.registro.resolver, sandbox=s.executor.status(),
                     ultima_versao=s.registro.ultima_versao)
        return {"valid": not a.erros, "issues": [i.model_dump() for i in a.issues], "port_types": a.port_types}

    @r.post("/fluxos/validar-conexao")
    def validar_conexao(corpo: ConexaoEntrada) -> dict[str, Any]:
        problemas = verificar_conexao(corpo.flow, corpo.connection, s.registro.resolver)
        return {"ok": not problemas, "issues": [i.model_dump() for i in problemas]}

    @r.post("/fluxos/exportar")
    def exportar(corpo: ExportarEntrada) -> dict[str, Any]:
        return exchange.exportar(s.registro, corpo.name, corpo.description, corpo.flow)

    # ----------------------------------------------------------------- execuções
    @r.post("/projetos/{pid}/execucoes", status_code=202)
    def executar(pid: str, corpo: ExecucaoEntrada) -> dict[str, Any]:
        projeto = s.store.obter_projeto(pid)
        if projeto is None:
            raise ApiError(404, "projeto_nao_encontrado", "Projeto não encontrado.")
        try:
            flow = corpo.flow or Flow.model_validate(projeto["flow"])
        except ValidationError:
            raise ApiError(422, "fluxo_invalido", "O fluxo salvo está corrompido.") from None
        run_id = s.motor.preparar(flow, pid, corpo.initial_data)
        s.motor.despachar(run_id)
        return s.store.obter_execucao(run_id)  # type: ignore[return-value]

    @r.get("/projetos/{pid}/execucoes")
    def historico(pid: str, limite: int = 30) -> list[dict[str, Any]]:
        if s.store.obter_projeto(pid) is None:
            raise ApiError(404, "projeto_nao_encontrado", "Projeto não encontrado.")
        return s.store.listar_execucoes(pid, max(1, min(limite, 100)))

    @r.get("/execucoes/{rid}")
    def obter_execucao(rid: str) -> dict[str, Any]:
        e = s.store.obter_execucao(rid)
        if e is None:
            raise ApiError(404, "execucao_nao_encontrada", "Execução não encontrada.")
        return e

    return r


def resposta_de_erro(exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=exc.corpo())


def tratar_validacao(request: Request, exc: Any) -> JSONResponse:  # RequestValidationError
    return JSONResponse(status_code=422, content={"error": {
        "code": "requisicao_invalida", "message": "Os dados enviados não são válidos.",
        "suggestion": None, "issues": custom_blocks.traduzir_erros(exc.errors())}})
