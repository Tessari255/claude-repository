"""Pool de contêineres aquecidos: esconde o custo de iniciar o Docker sem abrir mão de "um contêiner por trabalho".

O pool guarda até ``tamanho`` contêineres já iniciados, parados esperando o trabalho no stdin. Quem pede um trabalho
TIRA um deles (``retirar``); o contêiner nunca volta: ele roda aquele único trabalho e é removido pelo executor. Uma
thread própria repõe o pool em segundo plano, descarta os que morreram ou passaram do tempo ocioso e, se o Docker falhar,
espera cada vez mais entre as tentativas (o trabalho cai no caminho frio, sem erro para o usuário).

Este módulo não conhece o Docker: quem inicia e remove contêineres é o executor (``iniciar`` e ``descartar``).
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("trama.sandbox")

ESPERA_MAXIMA_S = 30.0  # teto da espera entre tentativas de repor depois de falhas seguidas
VARREDURA_S = 0.5  # de quanto em quanto tempo a thread confere mortos e vencidos


@dataclass
class Aquecido:
    """Um contêiner iniciado e parado, esperando o trabalho no stdin do cliente ``docker run``."""

    nome: str
    proc: subprocess.Popen[bytes]
    criado_em: float  # time.monotonic() de quando o cliente foi lançado (a vida ociosa conta daqui)


class PoolAquecido:
    def __init__(self, tamanho: int, ocioso_s: float, iniciar: Callable[[], Aquecido | None],
                 descartar: Callable[[list[Aquecido]], None], relogio: Callable[[], float] = time.monotonic) -> None:
        if tamanho > 0 and ocioso_s <= 0:
            raise ValueError("O tempo ocioso do pool precisa ser positivo.")
        self.tamanho = max(0, tamanho)
        self.ocioso_s = ocioso_s
        # O contêiner se mata em ``ocioso_s``; o host o troca um pouco antes, para nunca entregar um trabalho a quem está morrendo.
        self._vence_em = ocioso_s - min(2.0, ocioso_s / 4)
        self._iniciar = iniciar
        self._descartar = descartar
        self._relogio = relogio
        self._lock = threading.Lock()
        self._acordar = threading.Event()
        self._prontos: deque[Aquecido] = deque()
        self._iniciando = 0
        self._lancadores: set[threading.Thread] = set()
        self._ativo = False
        self._geracao = 0  # cada ativar() abre uma geração; uma thread de geração antiga se aposenta sozinha
        self._mantenedor: threading.Thread | None = None
        self._falhas = 0
        self._nao_antes = 0.0
        self._acertos = 0  # trabalhos que saíram de um contêiner aquecido
        self._faltas = 0  # trabalhos que pediram e não havia nenhum pronto (foram para o caminho frio)

    # ---------------------------------------------------------------- ciclo de vida
    def ativar(self) -> None:
        """Liga a reposição (idempotente; religa depois de ``encerrar``). Com tamanho 0 não faz nada."""
        if self.tamanho <= 0:
            return
        with self._lock:
            if self._ativo and self._mantenedor is not None and self._mantenedor.is_alive():
                return
            self._ativo = True
            self._geracao += 1
            self._falhas = 0
            self._nao_antes = 0.0
            self._mantenedor = threading.Thread(target=self._manter, args=(self._geracao,), name="trama-pool", daemon=True)
            self._mantenedor.start()

    def encerrar(self) -> None:
        """Para a reposição e remove todos os contêineres ociosos (inclusive os que ainda estavam subindo)."""
        with self._lock:
            self._ativo = False
            mantenedor = self._mantenedor
            lancadores = list(self._lancadores)
            sobras = list(self._prontos)
            self._prontos.clear()
        self._acordar.set()
        if mantenedor is not None and mantenedor is not threading.current_thread():
            mantenedor.join(timeout=5)
        for t in lancadores:
            t.join(timeout=40)  # quem termina de subir depois do encerramento é descartado por ele mesmo
        if sobras:
            self._descartar(sobras)

    # -------------------------------------------------------------------- uso
    def retirar(self) -> Aquecido | None:
        """Entrega o contêiner pronto mais antigo, ou None se não houver (o chamador usa o caminho frio)."""
        mortos: list[Aquecido] = []
        escolhido: Aquecido | None = None
        with self._lock:
            while self._prontos:
                candidato = self._prontos.popleft()
                if self._vivo(candidato):
                    escolhido = candidato
                    break
                mortos.append(candidato)
            if escolhido is None:
                self._faltas += 1
            else:
                self._acertos += 1
        if mortos:
            self._descartar(mortos)
        self._acordar.set()  # repor o que saiu (ou o que morreu)
        return escolhido

    def estado(self) -> dict[str, Any]:
        """Resumo seguro para a API: quantos estão configurados e quantos prontos agora."""
        with self._lock:
            prontos = sum(1 for q in self._prontos if self._vivo(q))
            return {"configurado": self.tamanho, "prontos": prontos, "ocioso_s": self.ocioso_s,
                    "acertos": self._acertos, "faltas": self._faltas}

    # --------------------------------------------------------------- internos
    def _vivo(self, q: Aquecido) -> bool:
        return q.proc.poll() is None and self._relogio() - q.criado_em < self._vence_em

    def _manter(self, geracao: int) -> None:
        while True:
            self._acordar.clear()
            descartados: list[Aquecido] = []
            with self._lock:
                if not self._ativo or geracao != self._geracao:
                    return
                restantes: deque[Aquecido] = deque()
                for q in self._prontos:
                    (restantes if self._vivo(q) else descartados).append(q)
                self._prontos = restantes
                faltam = self.tamanho - len(self._prontos) - self._iniciando
                novos = faltam if faltam > 0 and self._relogio() >= self._nao_antes else 0
                self._iniciando += novos
                for _ in range(novos):
                    t = threading.Thread(target=self._lancar, name="trama-pool-inicio", daemon=True)
                    self._lancadores.add(t)
                    t.start()
            if descartados:
                self._descartar(descartados)
            self._acordar.wait(timeout=VARREDURA_S)

    def _lancar(self) -> None:
        try:
            novo = self._iniciar()
        except Exception:
            log.exception("Falha inesperada ao iniciar um contêiner aquecido.")
            novo = None
        sobra: list[Aquecido] = []
        with self._lock:
            self._iniciando -= 1
            self._lancadores.discard(threading.current_thread())
            if novo is None:
                self._falhas += 1
                espera = min(ESPERA_MAXIMA_S, 2.0 ** (self._falhas - 1))
                self._nao_antes = self._relogio() + espera
                if self._falhas == 1:
                    log.warning("Não foi possível manter contêineres aquecidos; os trabalhos usam o caminho frio "
                                "(nova tentativa automática em %g s).", espera)
            elif not self._ativo:
                sobra.append(novo)
            else:
                self._falhas = 0
                self._prontos.append(novo)
        if sobra:
            self._descartar(sobra)
        self._acordar.set()
