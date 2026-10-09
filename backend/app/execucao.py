"""Estado de uma execução em andamento: o que o motor carrega de passo em passo.

Aqui não há E/S: nem banco, nem executor isolado. São só os dados que os passos leem e produzem, as exceções que
interrompem a execução e o resultado de cada passo.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from .errors import ErroBloco
from .models import BlockType, Flow, Passo

MAX_REGISTROS = 5000  # linhas de histórico (passos × repetições) por execução


def chave_etapa(step_id: str, iteracao: tuple[int, ...]) -> str:
    return step_id if not iteracao else f"{step_id}@{'.'.join(str(i) for i in iteracao)}"


class Cancelado(Exception):
    """O usuário cancelou a execução."""


class Encerrado(Exception):
    """Um passo “Encerrar” terminou a execução."""

    def __init__(self, estado: str, mensagem: str, passo: Passo) -> None:
        super().__init__(mensagem)
        self.estado, self.mensagem, self.passo = estado, mensagem, passo


@dataclass
class Resultado:
    """O que aconteceu com um passo: estado, se foi por tempo esgotado e as falhas que ninguém tratou."""

    estado: str  # concluido | falhou | ignorado
    expirou: bool = False
    falhas: list[dict[str, Any]] = field(default_factory=list)
    mensagem: str = ""  # texto curto do erro (para o “resultado de cada passo” do escopo)


@dataclass
class ResultadoLista:
    falhas: list[dict[str, Any]]
    resumo: list[dict[str, Any]]


@dataclass
class Execucao:
    run_id: str
    flow: Flow
    defs: dict[str, BlockType]
    port_types: dict[str, dict[str, dict[str, str]]]
    trigger_inputs: dict[str, Any]
    cancelar: threading.Event
    valores: dict[str, dict[str, Any]] = field(default_factory=dict)
    saidas_finais: list[dict[str, Any]] = field(default_factory=list)
    var_tipos: dict[str, str] = field(default_factory=dict)
    nomes: dict[str, str] = field(default_factory=dict)
    posicao: int = 0
    registros: int = 0
    max_registros: int = MAX_REGISTROS

    def checar_cancelamento(self) -> None:
        if self.cancelar.is_set():
            raise Cancelado()


def falha_de(passo_id: str, nome: str, e: ErroBloco) -> dict[str, Any]:
    """A falha como ela aparece no resultado da execução e no resumo dos contêineres."""
    return {"step_id": passo_id, "step_name": nome, "code": e.codigo, "message": e.mensagem,
            "suggestion": e.sugestao, "line": (e.tecnico or {}).get("line"), "technical": e.tecnico}
