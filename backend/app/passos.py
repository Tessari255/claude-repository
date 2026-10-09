"""Utilitários sobre a árvore de passos e a definição efetiva de cada passo."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .blocks.builtin import MAX_REGRAS
from .models import BlockType, Flow, Passo, PortDef, Regra


@dataclass(frozen=True)
class Posicao:
    """Onde um passo está na árvore: a lista que o contém e quais contêineres o envolvem."""

    passo: Passo
    pai: Passo | None          # contêiner dono da lista (None = lista principal do fluxo)
    espaco: str | None         # id do espaço (slot) do pai
    indice: int                # posição na lista
    ancestrais: tuple[str, ...]  # ids dos contêineres, do mais externo ao mais interno
    lista: tuple[Passo, ...]   # irmãos (inclui o próprio passo)


def percorrer(passos: list[Passo], pai: Passo | None = None, espaco: str | None = None,
              ancestrais: tuple[str, ...] = ()) -> Iterator[Posicao]:
    """Passos em ordem de documento (pré-ordem): o contêiner vem antes dos filhos."""
    lista = tuple(passos)
    for i, p in enumerate(passos):
        yield Posicao(p, pai, espaco, i, ancestrais, lista)
        for slot_id, filhos in p.slots.items():
            yield from percorrer(filhos, p, slot_id, (*ancestrais, p.id))


def todos_os_passos(flow: Flow, com_gatilho: bool = False) -> list[Passo]:
    base = [flow.trigger] if com_gatilho else []
    return base + [pos.passo for pos in percorrer(flow.steps)]


def achar(flow: Flow, step_id: str) -> Posicao | None:
    return next((pos for pos in percorrer(flow.steps) if pos.passo.id == step_id), None)


# --------------------------------------------------------------------------- definição efetiva
def portas_declaradas(valor: Any) -> tuple[list[PortDef], str | None]:
    """Lê um parâmetro "portas". Devolve (portas válidas, mensagem de erro ou None)."""
    if valor is None:
        return [], None
    if not isinstance(valor, list):
        return [], "A declaração precisa ser uma lista."
    if len(valor) > 20:
        return [], "São campos demais: o máximo é 20."
    portas: list[PortDef] = []
    for i, bruto in enumerate(valor, start=1):
        if not isinstance(bruto, dict):
            return portas, f"O item {i} da declaração é inválido."
        try:
            portas.append(PortDef.model_validate(bruto))
        except ValidationError as e:
            primeiro = e.errors()[0]
            campo = ".".join(str(x) for x in primeiro["loc"])
            msg = str(primeiro.get("msg", "valor inválido")).removeprefix("Value error, ")
            return portas, f"Item {i} da declaração ({campo or 'dados'}): {msg}."
    ids = [p.id for p in portas]
    repetidos = sorted({i for i in ids if ids.count(i) > 1})
    if repetidos:
        return portas, f"Há nomes repetidos na declaração: {', '.join(repetidos)}."
    return portas, None


def regras_declaradas(valor: Any) -> tuple[list[Regra], str | None]:
    """Lê o parâmetro "regras" de uma condição. Devolve (regras válidas, mensagem de erro ou None)."""
    if not isinstance(valor, list) or not valor:
        return [], "Adicione ao menos uma condição."
    if len(valor) > MAX_REGRAS:
        return [], f"São condições demais: o máximo é {MAX_REGRAS}."
    regras: list[Regra] = []
    for i, bruto in enumerate(valor, start=1):
        try:
            regras.append(Regra.model_validate(bruto))
        except ValidationError as e:
            primeiro = e.errors()[0]
            msg = str(primeiro.get("msg", "valor inválido")).removeprefix("Value error, ")
            return regras, f"A condição {i} é inválida: {msg}."
    return regras, None


def definicao_efetiva(tipo: BlockType, params: dict[str, Any]) -> BlockType:
    """A definição do bloco já com as entradas/saídas que o próprio passo declara (Python inline, gatilho)."""
    if not (tipo.inputs_from or tipo.outputs_from):
        return tipo
    mudancas: dict[str, Any] = {}
    if tipo.inputs_from:
        portas, _ = portas_declaradas(params.get(tipo.inputs_from, _padrao(tipo, tipo.inputs_from)))
        mudancas["inputs"] = portas
    if tipo.outputs_from:
        portas, _ = portas_declaradas(params.get(tipo.outputs_from, _padrao(tipo, tipo.outputs_from)))
        mudancas["outputs"] = portas
    if tipo.id == "builtin.python":
        mudancas["code"] = str(params.get("codigo") or _padrao(tipo, "codigo") or "")
    return tipo.model_copy(update=mudancas)


def _padrao(tipo: BlockType, param_id: str) -> Any:
    p = tipo.param(param_id)
    return p.default if p else None
