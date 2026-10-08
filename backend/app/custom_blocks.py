"""Criação e versionamento de blocos Python personalizados."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError

from .errors import ApiError
from .models import BlockDraft, BlockType
from .registry import Registro
from .sandbox import DockerExecutor
from .store import Store
from .engine import erro_da_sandbox


def erros_pydantic(exc: ValidationError) -> list[dict[str, Any]]:
    return traduzir_erros(exc.errors())


def traduzir_erros(erros: list[Any]) -> list[dict[str, Any]]:
    """Traduz erros do Pydantic/FastAPI em problemas legíveis (em português)."""
    traducoes = {
        "missing": "campo obrigatório ausente",
        "extra_forbidden": "campo desconhecido",
        "string_type": "deve ser um texto",
        "int_type": "deve ser um número inteiro",
        "int_parsing": "deve ser um número inteiro",
        "float_type": "deve ser um número",
        "float_parsing": "deve ser um número",
        "bool_type": "deve ser verdadeiro ou falso",
        "list_type": "deve ser uma lista",
        "dict_type": "deve ser um objeto",
        "string_too_long": "texto longo demais",
        "string_too_short": "texto curto demais",
        "too_long": "itens demais",
        "greater_than_equal": "valor abaixo do mínimo",
        "literal_error": "valor não permitido",
    }
    saida = []
    for e in erros:
        caminho = ".".join(str(p) for p in e["loc"] if p != "body")
        msg = traducoes.get(e["type"]) or str(e.get("msg", "valor inválido")).removeprefix("Value error, ")
        saida.append({"code": "campo_invalido", "severity": "erro", "scope": "estrutura",
                      "message": f"{caminho or 'conteúdo'}: {msg}", "param": caminho or None})
    return saida


def verificar_codigo(executor: DockerExecutor, codigo: str) -> dict[str, Any]:
    """Confere sintaxe e a presença de `run(inputs, params)` DENTRO do executor isolado."""
    r = executor.run("check", codigo)
    if r.ok:
        return {"verified": True, "ok": True, "error": None}
    if (r.error or {}).get("category") == "executor_indisponivel":
        return {"verified": False, "ok": None, "error": None,
                "message": (r.error or {}).get("message"), "suggestion": (r.error or {}).get("suggestion")}
    e = erro_da_sandbox(r.error or {})
    return {"verified": True, "ok": False, "error": e.como_dict()}


def _checar_declaracao(draft: BlockDraft) -> None:
    if not draft.outputs:
        raise ApiError(422, "declaracao_invalida", "Declare ao menos uma saída para o bloco.",
                       sugestao="As saídas são os nomes das chaves do dicionário devolvido por run().")
    if any(o.conditional for o in draft.outputs) or any(i.type_from for i in draft.inputs + draft.outputs):
        raise ApiError(422, "declaracao_invalida",
                       "Blocos personalizados não podem usar saídas condicionais nem tipos dinâmicos.")


def _salvar(store: Store, executor: DockerExecutor, tipo: BlockType, nova_versao: bool = False) -> dict[str, Any]:
    avisos: list[str] = []
    check = verificar_codigo(executor, tipo.code or "")
    if check["verified"] and not check["ok"]:
        err = check["error"]
        raise ApiError(422, "codigo_invalido", err["message"], sugestao=err.get("suggestion"),
                       problemas=[{"code": err["code"], "severity": "erro", "scope": "configuracao",
                                   "message": err["message"], "technical": err.get("technical")}])
    if not check["verified"]:
        avisos.append("O código não pôde ser verificado porque o executor isolado está indisponível. "
                      "O bloco foi salvo, mas só poderá ser testado e executado quando o executor estiver disponível.")
    salvo = store.inserir_nova_versao(tipo) if nova_versao else store.inserir_tipo(tipo)
    return {"block": salvo.model_dump(), "warnings": avisos}


def criar_bloco(store: Store, executor: DockerExecutor, draft: BlockDraft) -> dict[str, Any]:
    _checar_declaracao(draft)
    tipo = draft.para_tipo(f"custom.{uuid.uuid4().hex[:10]}", 1)
    return _salvar(store, executor, tipo)


def nova_versao(store: Store, registro: Registro, executor: DockerExecutor, type_id: str,
                draft: BlockDraft) -> dict[str, Any]:
    """Salva uma NOVA versão. Fluxos existentes continuam na versão que fixaram."""
    if not type_id.startswith("custom."):
        raise ApiError(403, "bloco_interno", "Blocos internos não podem ser alterados.")
    if registro.ultima_versao(type_id) is None:
        raise ApiError(404, "bloco_nao_encontrado", "Bloco não encontrado.")
    _checar_declaracao(draft)
    return _salvar(store, executor, draft.para_tipo(type_id, 1), nova_versao=True)  # a versão real é atribuída na gravação


def excluir_bloco(store: Store, type_id: str) -> None:
    if not type_id.startswith("custom."):
        raise ApiError(403, "bloco_interno", "Blocos internos não podem ser excluídos.")
    em_uso = store.projetos_que_usam(type_id)
    if em_uso:
        raise ApiError(409, "bloco_em_uso",
                       f"Este bloco está em uso nos projetos: {', '.join(sorted(set(em_uso)))}.",
                       sugestao="Remova o bloco desses fluxos antes de excluí-lo, para não quebrar fluxos existentes.")
    if not store.excluir_tipo(type_id):
        raise ApiError(404, "bloco_nao_encontrado", "Bloco não encontrado.")
