"""Modelos de dados: definição de tipos de bloco, fluxo e problemas de validação.

As chaves do JSON são em inglês (como o contrato ``inputs``/``params``); valores,
identificadores de domínio e mensagens são em português.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .tipos import TIPOS_DADO, validar_json_puro

TipoParametro = Literal["texto", "numero", "booleano", "lista", "json", "selecao", "codigo"]
Estado = Literal["aguardando", "executando", "concluido", "falhou", "ignorado"]

RE_ID_PORTA = re.compile(r"^[a-z_][a-z0-9_]{0,39}$")
RE_ID_BLOCO = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
RE_ID_TIPO = re.compile(r"^(builtin|custom)\.[a-z0-9_\-]{1,48}$")

MAX_BLOCOS = 200
MAX_CONEXOES = 600
MAX_CODIGO = 64 * 1024
MAX_VERSAO = 1_000_000


class Estrito(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- definição de bloco
class TypeFrom(Estrito):
    """O tipo da porta/parâmetro depende de outro elemento do mesmo bloco."""

    param: str | None = None  # usa o valor deste parâmetro (ex.: "tipo" da constante)
    input: str | None = None  # herda o tipo da saída conectada a esta entrada


class Option(Estrito):
    value: str
    label: str


class VisibleWhen(Estrito):
    """O parâmetro só vale (e só aparece) quando outro parâmetro tem um destes valores."""

    param: str
    values: list[str]


class PortDef(Estrito):
    id: str
    label: str = Field(min_length=1, max_length=60)
    type: str = "qualquer"
    required: bool = True  # só faz sentido para entradas
    description: str = Field(default="", max_length=300)
    type_from: TypeFrom | None = None
    conditional: bool = False  # saída que só existe quando o caminho é escolhido

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not RE_ID_PORTA.match(v):
            raise ValueError("use letras minúsculas, números e _ (começando por letra), até 40 caracteres")
        return v

    @field_validator("type")
    @classmethod
    def _tipo(cls, v: str) -> str:
        if v not in TIPOS_DADO:
            raise ValueError(f"tipo desconhecido: {v}")
        return v


class ParamDef(Estrito):
    id: str
    label: str = Field(min_length=1, max_length=60)
    type: TipoParametro = "texto"
    required: bool = False
    default: Any = None
    options: list[Option] = Field(default_factory=list)
    help: str = Field(default="", max_length=400)
    placeholder: str = Field(default="", max_length=100)
    multiline: bool = False
    allow_empty: bool = False
    min: float | None = None
    max: float | None = None
    type_from: TypeFrom | None = None
    visible_when: VisibleWhen | None = None

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not RE_ID_PORTA.match(v):
            raise ValueError("use letras minúsculas, números e _ (começando por letra), até 40 caracteres")
        return v

    @model_validator(mode="after")
    def _selecao(self) -> "ParamDef":
        if self.type == "selecao" and not self.options:
            raise ValueError("parâmetros do tipo seleção precisam de ao menos uma opção")
        return self


def _conferir_ids_unicos(entradas: list, saidas: list, params: list) -> None:
    for nome, itens in (("entradas", entradas), ("saídas", saidas), ("parâmetros", params)):
        ids = [i.id for i in itens]
        repetidos = sorted({i for i in ids if ids.count(i) > 1})
        if repetidos:
            raise ValueError(f"identificadores repetidos em {nome}: {', '.join(repetidos)}")


class BlockType(Estrito):
    id: str
    version: int = Field(ge=1, le=MAX_VERSAO)
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=500)
    category: str = Field(default="Personalizados", min_length=1, max_length=40)
    kind: Literal["builtin", "python"]
    icon: str | None = None
    inputs: list[PortDef] = Field(default_factory=list, max_length=20)
    outputs: list[PortDef] = Field(default_factory=list, max_length=20)
    params: list[ParamDef] = Field(default_factory=list, max_length=20)
    code: str | None = Field(default=None, max_length=MAX_CODIGO)
    created_at: str | None = None

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not RE_ID_TIPO.match(v):
            raise ValueError("identificador inválido")
        return v

    @model_validator(mode="after")
    def _unicos(self) -> "BlockType":
        _conferir_ids_unicos(self.inputs, self.outputs, self.params)
        if self.kind == "python" and not (self.code or "").strip():
            raise ValueError("blocos Python precisam de código")
        return self

    def input(self, port_id: str) -> PortDef | None:
        return next((p for p in self.inputs if p.id == port_id), None)

    def output(self, port_id: str) -> PortDef | None:
        return next((p for p in self.outputs if p.id == port_id), None)

    def param(self, param_id: str) -> ParamDef | None:
        return next((p for p in self.params if p.id == param_id), None)


class BlockDraft(Estrito):
    """Corpo enviado pelo editor de blocos (id e versão são atribuídos pelo servidor)."""

    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=500)
    category: str = Field(default="Personalizados", min_length=1, max_length=40)
    inputs: list[PortDef] = Field(default_factory=list, max_length=20)
    outputs: list[PortDef] = Field(default_factory=list, max_length=20)
    params: list[ParamDef] = Field(default_factory=list, max_length=20)
    code: str = Field(min_length=1, max_length=MAX_CODIGO)

    @model_validator(mode="after")
    def _unicos(self) -> "BlockDraft":
        _conferir_ids_unicos(self.inputs, self.outputs, self.params)
        return self

    def para_tipo(self, type_id: str, version: int) -> BlockType:
        return BlockType(
            id=type_id, version=version, name=self.name, description=self.description,
            category=self.category, kind="python", icon="python",
            inputs=self.inputs, outputs=self.outputs, params=self.params, code=self.code,
        )


# --------------------------------------------------------------------------- fluxo
class Position(Estrito):
    x: float
    y: float


class Viewport(Estrito):
    x: float = 0
    y: float = 0
    zoom: float = 1


class BlockInstance(Estrito):
    id: str
    type: str = Field(max_length=80)
    version: int = Field(ge=1, le=MAX_VERSAO)
    position: Position
    params: dict[str, Any] = Field(default_factory=dict)
    label: str | None = Field(default=None, max_length=80)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not RE_ID_BLOCO.match(v):
            raise ValueError("identificador inválido (use letras, números, _ e -)")
        return v

    @field_validator("params")
    @classmethod
    def _params(cls, v: dict[str, Any]) -> dict[str, Any]:
        # profundidade limitada, sem NaN/infinito e sem surrogates: evita dados que gravam mas não leem de volta
        try:
            validar_json_puro(v)
        except ValueError as e:
            raise ValueError(f"configuração inválida: {e}") from None
        return v


class Endpoint(Estrito):
    block: str
    port: str


class Connection(Estrito):
    id: str
    source: Endpoint
    target: Endpoint

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not RE_ID_BLOCO.match(v):
            raise ValueError("identificador inválido (use letras, números, _ e -)")
        return v


class Flow(Estrito):
    schema_version: int = 1
    blocks: list[BlockInstance] = Field(default_factory=list, max_length=MAX_BLOCOS)
    connections: list[Connection] = Field(default_factory=list, max_length=MAX_CONEXOES)
    viewport: Viewport | None = None

    @field_validator("schema_version")
    @classmethod
    def _versao(cls, v: int) -> int:
        if v != 1:
            raise ValueError(f"versão de formato {v} não é suportada por esta versão da Trama")
        return v

    def bloco(self, block_id: str) -> BlockInstance | None:
        return next((b for b in self.blocks if b.id == block_id), None)


# --------------------------------------------------------------------------- validação
class Issue(BaseModel):
    code: str
    severity: Literal["erro", "aviso"] = "erro"
    # "estrutura": impede salvar/conectar (ciclo, tipos incompatíveis, ligação inválida);
    # "configuracao": rascunho salvável, mas impede a execução (campo obrigatório etc.).
    scope: Literal["estrutura", "configuracao"] = "configuracao"
    message: str
    hint: str | None = None
    block_id: str | None = None
    port: str | None = None
    param: str | None = None
    connection_id: str | None = None
    connection_ids: list[str] = Field(default_factory=list)
