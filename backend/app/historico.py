"""Histórico de execuções: o único ponto por onde o motor grava a execução e as linhas de cada passo.

Concentrar a gravação aqui é o que permite, depois, avisar quem acompanha a execução (tempo real) sem
espalhar essa preocupação pelo motor: basta reagir a estes métodos. A leitura continua direto no ``Store``.
"""

from __future__ import annotations

from typing import Any

from .errors import ErroBloco
from .execucao import Execucao, chave_etapa
from .store import Store


class Historico:
    def __init__(self, store: Store) -> None:
        self._store = store

    # ------------------------------------------------------------------ a execução
    def criar_execucao(self, *, kind: str, project_id: str | None, snapshot: dict[str, Any], etapas: list[str],
                       trigger_inputs: dict[str, Any] | None = None) -> str:
        return self._store.criar_execucao(kind=kind, project_id=project_id, snapshot=snapshot, etapas=etapas,
                                          trigger_inputs=trigger_inputs)

    def atualizar_execucao(self, run_id: str, **campos: Any) -> None:
        self._store.atualizar_execucao(run_id, **campos)

    def encerrar_etapas_abertas(self, run_id: str, motivo: str, estado_aberto: str = "falhou") -> None:
        self._store.encerrar_etapas_abertas(run_id, motivo, estado_aberto)

    # ------------------------------------------------------------------ as linhas dos passos
    def atualizar_etapa(self, run_id: str, chave: str, **campos: Any) -> None:
        """Atualiza uma linha que já existe (a do passo em sua primeira repetição, criada junto com a execução)."""
        self._store.atualizar_etapa(run_id, chave, **campos)

    def gravar_etapa(self, ex: Execucao, step_id: str, iteracao: tuple[int, ...], **campos: Any) -> None:
        """Atualiza a linha do passo nesta repetição; cria a linha se ela ainda não existe (passos de laços)."""
        chave = chave_etapa(step_id, iteracao)
        if self._store.garantir_etapa(ex.run_id, chave, step_id, list(iteracao), ex.posicao):
            ex.posicao += 1
            ex.registros += 1
            if ex.registros > ex.max_registros:
                raise ErroBloco(
                    f"A execução passou do limite de {ex.max_registros} registros de passos (passos × repetições).",
                    codigo="registros_demais", sugestao="Reduza o limite de itens do laço ou os passos de dentro dele.")
        self._store.atualizar_etapa(ex.run_id, chave, **campos)
