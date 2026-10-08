"""Modelos de dados (formato 2): definição de blocos, fluxo em passos e problemas de validação.

Um fluxo tem um **gatilho** e uma lista de **passos** que rodam em sequência, como no Power Automate.
Alguns passos são contêineres (condição, para cada, escopo…) e têm listas de passos filhos (``slots``).
Os dados passam de um passo ao outro por **conteúdo dinâmico**: um campo guarda um valor fixo ou uma
referência à saída de um passo anterior (``Ref``), nunca uma "ligação" entre portas.

As chaves do JSON são em inglês (como o contrato ``inputs``/``params``); valores, identificadores de
domínio e mensagens são em português.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator

from .tipos import TIPOS_DADO, validar_json_puro

TipoParametro = Literal[
    "texto", "numero", "booleano", "lista", "json", "selecao", "codigo",
    "regras",    # lista de comparações da Condição / Repetir até (editor próprio)
    "portas",    # declaração de campos/entradas/saídas (Python inline, gatilho)
    "variavel",  # escolhe uma variável já inicializada no fluxo
]
Estado = Literal["aguardando", "executando", "concluido", "falhou", "ignorado", "cancelado"]
ExecutarApos = Literal["sucesso", "falhou", "ignorado", "expirou"]

RE_ID_PORTA = re.compile(r"^[a-z_][a-z0-9_]{0,39}$")
RE_ID_PASSO = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
RE_ID_TIPO = re.compile(r"^(builtin|custom)\.[a-z0-9_\-]{1,48}$")

ID_GATILHO = "gatilho"
MAX_PASSOS = 200
MAX_PROFUNDIDADE = 8
MAX_CODIGO = 64 * 1024
MAX_VERSAO = 1_000_000
MAX_PARTES = 60
MAX_TENTATIVAS = 5


class Estrito(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- definição de bloco
class TypeFrom(Estrito):
    """O tipo da porta/parâmetro depende de outro elemento do mesmo bloco."""

    param: str | None = None  # usa o valor deste parâmetro (ex.: "tipo" da variável)
    input: str | None = None  # herda o tipo do campo preenchido nesta entrada


class Option(Estrito):
    value: str
    label: str


class VisibleWhen(Estrito):
    """O parâmetro só vale (e só aparece) quando outro parâmetro tem um destes valores."""

    param: str
    values: list[str]


class SlotDef(Estrito):
    """Uma lista de passos filhos de um contêiner (ex.: “Se sim” e “Se não” de uma condição)."""

    id: str
    label: str = Field(min_length=1, max_length=60)
    # Passos de um espaço "transparente" continuam visíveis (para o conteúdo dinâmico) depois do contêiner.
    transparent: bool = False
    empty_hint: str = Field(default="", max_length=200)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not RE_ID_PORTA.match(v):
            raise ValueError("identificador inválido")
        return v


class PortDef(Estrito):
    id: str
    label: str = Field(min_length=1, max_length=60)
    type: str = "qualquer"
    required: bool = True  # só faz sentido para entradas
    description: str = Field(default="", max_length=300)
    type_from: TypeFrom | None = None
    # Legado do formato 1 (saídas condicionais). Mantido só para ler blocos gravados antes; nunca é usado.
    conditional: bool = False
    # Saída que só existe DENTRO do contêiner (ex.: o item atual do "Para cada").
    inside: bool = False
    # Valor inicial sugerido ao adicionar o bloco (entradas do gatilho, quantidade a incrementar…).
    default: Any = None

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

    @field_validator("default")
    @classmethod
    def _padrao(cls, v: Any) -> Any:
        validar_json_puro(v)
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
    # --- formato 2
    slots: list[SlotDef] = Field(default_factory=list, max_length=4)  # contêiner se não vazio
    inputs_from: str | None = None   # parâmetro ("portas") que declara as entradas deste passo
    outputs_from: str | None = None  # parâmetro ("portas") que declara as saídas deste passo
    trigger: bool = False            # gatilho: só pode ser o gatilho do fluxo, nunca um passo

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

    @property
    def conteiner(self) -> bool:
        return bool(self.slots)

    def input(self, port_id: str) -> PortDef | None:
        return next((p for p in self.inputs if p.id == port_id), None)

    def output(self, port_id: str) -> PortDef | None:
        return next((p for p in self.outputs if p.id == port_id), None)

    def param(self, param_id: str) -> ParamDef | None:
        return next((p for p in self.params if p.id == param_id), None)

    def slot(self, slot_id: str) -> SlotDef | None:
        return next((s for s in self.slots if s.id == slot_id), None)


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


# --------------------------------------------------------------------------- campos e conteúdo dinâmico
class Ref(Estrito):
    """Conteúdo dinâmico: a saída ``output`` do passo ``step`` (opcionalmente um caminho dentro dela)."""

    step: str = Field(max_length=64)
    output: str = Field(max_length=40)
    path: str = Field(default="", max_length=200)  # ex.: endereco.cidade ou itens.0


class Campo(Estrito):
    """O valor de uma entrada: ``{"value": X}`` (valor fixo) ou ``{"parts": [...]}`` (conteúdo dinâmico).

    Em ``parts``, textos soltos e referências se alternam, como os “chips” do Power Automate. Uma única
    referência preserva o tipo do dado; qualquer outra combinação vira um texto.
    """

    value: Any = None
    parts: list[str | Ref] | None = Field(default=None, max_length=MAX_PARTES)

    @model_validator(mode="after")
    def _um_dos_dois(self) -> "Campo":
        tem_valor = "value" in self.model_fields_set
        if tem_valor == (self.parts is not None):
            raise ValueError("informe um valor fixo (value) ou conteúdo dinâmico (parts), e não os dois")
        try:
            validar_json_puro(self.value if tem_valor else [p for p in (self.parts or []) if isinstance(p, str)])
        except ValueError as e:
            raise ValueError(f"valor inválido: {e}") from None
        return self

    @model_serializer(mode="plain")
    def _serializar(self) -> dict[str, Any]:
        """Só a chave ativa: ``{"value": X}`` ou ``{"parts": [...]}`` (nunca as duas, para o dado reler igual)."""
        if self.parts is not None:
            return {"parts": [p if isinstance(p, str) else p.model_dump() for p in self.parts]}
        return {"value": self.value}

    @property
    def dinamico(self) -> bool:
        return self.parts is not None

    def referencias(self) -> list[Ref]:
        return [p for p in (self.parts or []) if isinstance(p, Ref)]


class Regra(Estrito):
    """Uma comparação da Condição / Repetir até: ``esq`` <operador> ``dir`` (testes como “está vazio” não usam ``dir``)."""

    esq: Campo
    op: str = Field(max_length=20)
    dir: Campo | None = None


# --------------------------------------------------------------------------- passos e fluxo
class Tentativas(Estrito):
    count: int = Field(default=0, ge=0, le=MAX_TENTATIVAS)  # novas tentativas depois da primeira falha
    interval_s: float = Field(default=2.0, ge=0, le=30)


class Configuracoes(Estrito):
    retry: Tentativas = Field(default_factory=Tentativas)
    timeout_s: float | None = Field(default=None, gt=0, le=3600)  # só vale para código Python


class Passo(Estrito):
    id: str
    type: str = Field(max_length=80)
    version: int = Field(ge=1, le=MAX_VERSAO)
    label: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=500)
    inputs: dict[str, Campo] = Field(default_factory=dict, max_length=40)
    params: dict[str, Any] = Field(default_factory=dict)
    # Em quais situações do passo anterior (da mesma lista) este passo roda. Padrão: só se teve sucesso.
    run_after: list[ExecutarApos] = Field(default_factory=lambda: ["sucesso"], min_length=1, max_length=4)
    settings: Configuracoes = Field(default_factory=Configuracoes)
    slots: dict[str, list["Passo"]] = Field(default_factory=dict, max_length=4)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not RE_ID_PASSO.match(v):
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

    @field_validator("run_after")
    @classmethod
    def _sem_repetidos(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("situações repetidas em “executar após”")
        return v

    @field_validator("inputs")
    @classmethod
    def _ids_das_entradas(cls, v: dict[str, Campo]) -> dict[str, Campo]:
        for chave in v:
            if not RE_ID_PORTA.match(chave):
                raise ValueError(f"identificador de entrada inválido: {chave!r}")
        return v

    @field_validator("slots")
    @classmethod
    def _ids_dos_espacos(cls, v: dict[str, list["Passo"]]) -> dict[str, list["Passo"]]:
        for chave in v:
            if not RE_ID_PORTA.match(chave):
                raise ValueError(f"identificador de espaço inválido: {chave!r}")
        return v


Passo.model_rebuild()


def gatilho_padrao() -> Passo:
    return Passo(id=ID_GATILHO, type="builtin.gatilho_manual", version=1, params={"campos": []})


class Flow(Estrito):
    schema_version: int = 2
    trigger: Passo = Field(default_factory=gatilho_padrao)
    steps: list[Passo] = Field(default_factory=list, max_length=MAX_PASSOS)

    @field_validator("schema_version")
    @classmethod
    def _versao(cls, v: int) -> int:
        if v != 2:
            raise ValueError(f"versão de formato {v} não é suportada por esta versão da Trama")
        return v

    @model_validator(mode="after")
    def _limites(self) -> "Flow":
        total = 0
        pilha: list[tuple[list[Passo], int]] = [(self.steps, 1)]
        while pilha:
            lista, nivel = pilha.pop()
            if nivel > MAX_PROFUNDIDADE:
                raise ValueError(f"os passos estão aninhados demais (máximo de {MAX_PROFUNDIDADE} níveis)")
            for p in lista:
                total += 1
                if total > MAX_PASSOS:
                    raise ValueError(f"o fluxo tem passos demais (máximo de {MAX_PASSOS})")
                for filhos in p.slots.values():
                    pilha.append((filhos, nivel + 1))
        if self.trigger.slots:
            raise ValueError("o gatilho não pode ter passos dentro dele")
        return self


# --------------------------------------------------------------------------- validação
class Issue(BaseModel):
    code: str
    severity: Literal["erro", "aviso"] = "erro"
    # "estrutura": impede salvar (ids repetidos, aninhamento inválido…);
    # "configuracao": rascunho salvável, mas impede a execução (campo obrigatório, conteúdo dinâmico inválido…).
    scope: Literal["estrutura", "configuracao"] = "configuracao"
    message: str
    hint: str | None = None
    step_id: str | None = None
    field: str | None = None  # entrada ou parâmetro com o problema
