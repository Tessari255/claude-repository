"""Exportação e importação de fluxos em JSON.

O arquivo exportado carrega as definições (contrato + código) dos blocos personalizados
usados, nas versões exatas fixadas pelo fluxo, para que o comportamento seja o mesmo em
outra instalação. Na importação nada é confiado: o conteúdo é validado, e o código
importado só roda no executor isolado, como qualquer outro.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from .custom_blocks import erros_pydantic
from .errors import ApiError
from .models import BlockType, Flow
from .registry import Registro
from .store import Store, agora
from .validation import analisar

FORMATO = "trama.fluxo"
TAMANHO_MAX = 5 * 1024 * 1024


class InfoProjeto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = ""


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["trama.fluxo"]
    format_version: Literal[1]
    exported_at: str | None = None
    project: InfoProjeto
    flow: Flow
    custom_blocks: list[BlockType] = []


def exportar(registro: Registro, nome: str, descricao: str, flow: Flow) -> dict[str, Any]:
    usados = sorted({(b.type, b.version) for b in flow.blocks if b.type.startswith("custom.")})
    blocos: list[dict[str, Any]] = []
    for type_id, versao in usados:
        tipo = registro.resolver(type_id, versao)
        if tipo is None:
            raise ApiError(422, "bloco_desconhecido",
                           f"O fluxo usa o bloco {type_id} (v{versao}), que não existe mais; não é possível exportar.")
        blocos.append(tipo.model_dump(exclude={"created_at"}))
    return {"format": FORMATO, "format_version": 1, "exported_at": agora(),
            "project": {"name": nome, "description": descricao},
            "flow": flow.model_dump(), "custom_blocks": blocos}


def _conteudo(t: BlockType) -> dict[str, Any]:
    return t.model_dump(exclude={"created_at"})


def importar(store: Store, registro: Registro, bruto: Any, *, aplicar: bool = True) -> dict[str, Any]:
    """Valida e (se `aplicar`) grava os blocos personalizados. Devolve nome, fluxo e avisos.

    Nada é gravado se qualquer validação falhar.
    """
    if not isinstance(bruto, dict):
        raise ApiError(422, "arquivo_invalido", "O arquivo não parece ser um fluxo da Trama.",
                       sugestao="Escolha um arquivo .json exportado pela própria Trama.")
    try:
        env = Envelope.model_validate(bruto)
    except ValidationError as e:
        raise ApiError(422, "arquivo_invalido", "O conteúdo do arquivo não é um fluxo válido da Trama.",
                       problemas=erros_pydantic(e), sugestao="Escolha um arquivo .json exportado pela própria Trama.")

    avisos: list[str] = []
    for t in env.custom_blocks:
        if t.kind != "python" or not t.id.startswith("custom."):
            raise ApiError(422, "arquivo_invalido", f"O bloco “{t.name}” do arquivo não é um bloco personalizado válido.")
        if any(o.conditional for o in t.outputs) or any(p.type_from for p in [*t.inputs, *t.outputs]):
            raise ApiError(422, "arquivo_invalido", f"O bloco “{t.name}” usa recursos que blocos personalizados não podem usar.")

    # --- plano: reutilizar, inserir com o mesmo id ou importar como cópia
    novos: list[BlockType] = []
    remap: dict[tuple[str, int], tuple[str, int]] = {}
    ids_no_arquivo = {(t.id, t.version) for t in env.custom_blocks}
    for t in env.custom_blocks:
        existente = store.obter_tipo(t.id, t.version)
        if existente is not None and _conteudo(existente) == _conteudo(t):
            continue  # idêntico: reutiliza
        if existente is None and store.ultima_versao(t.id) is None:
            novos.append(t)  # id livre: preserva identificador e versão
            continue
        # O id já existe com outro conteúdo: importa como cópia para não alterar nada em silêncio.
        copia_id = f"custom.{uuid.uuid4().hex[:10]}"
        novos.append(t.model_copy(update={"id": copia_id, "version": 1}))
        remap[(t.id, t.version)] = (copia_id, 1)
        avisos.append(f"O bloco “{t.name}” já existia com conteúdo diferente; foi importado como uma cópia separada.")

    flow = env.flow.model_copy(deep=True)
    for b in flow.blocks:
        if (b.type, b.version) in remap:
            b.type, b.version = remap[(b.type, b.version)]
        elif b.type.startswith("custom.") and (b.type, b.version) not in ids_no_arquivo \
                and registro.resolver(b.type, b.version) is None:
            raise ApiError(422, "bloco_ausente",
                           f"O fluxo usa o bloco personalizado “{b.label or b.type}”, mas o arquivo não o inclui.",
                           sugestao="Exporte o fluxo novamente na instalação de origem.")

    sobreposicao = {(t.id, t.version): t for t in novos}

    def resolver(type_id: str, version: int) -> BlockType | None:
        return sobreposicao.get((type_id, version)) or registro.resolver(type_id, version)

    analise = analisar(flow, resolver)
    estrutura = analise.erros_de_estrutura
    if estrutura:
        raise ApiError(422, "fluxo_invalido", "O fluxo do arquivo tem problemas e não foi importado.",
                       problemas=[i.model_dump() for i in estrutura])

    if aplicar and novos:
        store.inserir_tipos(novos)
    return {"name": env.project.name, "description": env.project.description,
            "flow": flow.model_dump(), "warnings": avisos,
            "created_blocks": [t.name for t in novos]}
