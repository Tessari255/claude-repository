"""Lote no executor isolado: um laço inteiro em UM contêiner (modo ``batch`` do runner).

Aqui ficam as peças que não precisam do Docker: quanto tempo e quantos bytes um lote pode ter, como ler aos poucos as
linhas que o runner escreve (uma por item) e como entregá-las a quem pediu o lote, em ordem, parando na primeira falha.
Quem inicia, vigia e abate o contêiner é o ``DockerExecutor``.

A resposta do runner é tratada como não confiável, como no trabalho avulso: o código do usuário roda no mesmo processo
do runner e pode forjar linhas. Por isso uma linha só vale se for a do próximo item esperado; o resto é ignorado, e o
conteúdo de cada linha passa pela mesma conferência (tipos, tamanhos, JSON) de uma resposta avulsa.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..config import Limites

if TYPE_CHECKING:
    from .executor import SandboxResult

AoItem = Callable[[int, "SandboxResult"], bool]


def tempo_do_lote(limites: Limites, itens: int) -> float:
    """Tempo total de um lote: o tempo de um item vezes a quantidade de itens, até o máximo configurável.

    Nunca é menor que o tempo de um item, mesmo que o máximo configurado seja."""
    return max(limites.tempo_s, min(itens * limites.tempo_s, limites.lote_tempo_max_s))


def corpo_max_do_lote(limites: Limites) -> int:
    """Quantos bytes de entradas (JSON) um lote leva. Os itens ficam todos na memória do runner enquanto o lote roda,
    então o teto também acompanha o limite de memória (1/32 dele): o código de um item perde só uma fração do que tinha."""
    return min(limites.lote_corpo_max, limites.memoria_mb * 1024 * 1024 // 32)


class LeitorDeLote:
    """Tira, aos poucos, do começo de um buffer as linhas que o runner escreve no modo lote."""

    def __init__(self, prefixo: str, token: str) -> None:
        self._marcador = (prefixo + token).encode("utf-8")

    def ler(self, buffer: bytearray) -> tuple[list[dict[str, Any]], int]:
        """Consome de ``buffer`` as linhas completas do runner e devolve (objetos JSON, bytes de ruído descartados).

        Ruído é tudo o que veio fora de uma linha do runner (texto escrito direto no descritor 1, linhas ilegíveis).
        Uma linha que ainda não terminou fica no buffer para a próxima chamada."""
        objetos: list[dict[str, Any]] = []
        ruido = 0
        while True:
            inicio = buffer.find(self._marcador)
            if inicio < 0:
                # o marcador pode estar chegando partido ao meio: guarda só o que ainda pode virar o começo dele
                corte = max(0, len(buffer) - (len(self._marcador) - 1))
                ruido += corte
                del buffer[:corte]
                return objetos, ruido
            fim = buffer.find(b"\n", inicio)
            if fim < 0:
                ruido += inicio
                del buffer[:inicio]
                return objetos, ruido
            texto = bytes(buffer[inicio + len(self._marcador):fim]).decode("utf-8", "replace")
            ruido += inicio
            del buffer[:fim + 1]
            try:
                objeto = json.loads(texto)
            except ValueError:
                ruido += fim + 1 - inicio
                continue
            if isinstance(objeto, dict):
                objetos.append(objeto)
            else:
                ruido += fim + 1 - inicio


class EntregaDoLote:
    """Entrega ao ``ao_item`` o resultado de cada item, em ordem, e sabe quando o lote não deve receber mais nada.

    O lote se encerra no primeiro item que falha, quando ``ao_item`` pede para parar (devolve False) ou quando todos os
    itens foram entregues. Linhas fora de ordem, repetidas ou de itens que não existem são ignoradas."""

    def __init__(self, itens: int, ao_item: AoItem, converter: Callable[[dict[str, Any], int], SandboxResult]) -> None:
        self.itens = itens
        self.entregues = 0
        self.falhou = False
        self.parado = False
        self.fim = False  # o runner avisou que terminou (ele também avisa depois de uma falha)
        self.marco = time.monotonic()  # quando começou o item em andamento: a última entrega, ou o início do lote
        self._ao_item = ao_item
        self._converter = converter

    @property
    def aberto(self) -> bool:
        """Falta o resultado de algum item, e ainda é para recebê-lo."""
        return not (self.falhou or self.parado or self.entregues >= self.itens)

    def aceitar(self, linha: dict[str, Any]) -> None:
        """Uma linha lida do runner: o aviso de fim, ou o resultado do item que se espera agora."""
        if linha.get("fim") is True:
            self.fim = True
            return
        if not self.aberto:
            return
        posicao = linha.get("i")
        # Uma falha sem posição é o runner dizendo que nem chegou a rodar (entrada ilegível, limite impossível): é do item em andamento.
        e_o_proximo = type(posicao) is int and posicao == self.entregues
        falha_sem_posicao = posicao is None and linha.get("ok") is False
        if e_o_proximo or falha_sem_posicao:
            self.entregar(self._converter(linha, int((time.monotonic() - self.marco) * 1000)))

    def entregar(self, resultado: SandboxResult) -> None:
        """Entrega o resultado do item em andamento (também usado para os erros que o host mesmo constata)."""
        posicao = self.entregues
        self.entregues += 1
        if not resultado.ok:
            self.falhou = True
        if self._ao_item(posicao, resultado) is False:
            self.parado = True
        self.marco = time.monotonic()  # o tempo que o chamador gasta com o resultado não conta para o próximo item
