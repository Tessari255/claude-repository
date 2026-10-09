"""Para cada em lote: quando o corpo do laço é só um passo Python, todas as iterações rodam em UM contêiner.

Hoje cada iteração paga um contêiner (≈0,3 s a 1 s). As entradas de cada iteração só dependem do item, da posição e do que
está fora do laço, então dá para resolver todas antes e mandar uma só tarefa ao executor, que devolve um resultado por item.

Isto muda COMO o código roda, não o que fica registrado. Cada resultado volta pelo caminho normal de um passo (o despacho, as
conferências da resposta do executor, o histórico), como se o executor tivesse sido chamado naquela iteração: a linha
``passo@i`` de cada iteração, com entradas, saídas, logs e erro, e a falha da primeira iteração que falha interrompendo as
seguintes, são as de antes. Qualquer corpo que não se encaixe, ou qualquer coisa que impeça o lote (executor sem suporte,
entradas que não resolvem, entradas grandes demais), devolve ``None`` e o laço segue o caminho de sempre.

Diferença que fica: cancelar abate o contêiner na hora, então a iteração em andamento termina como cancelada em vez de
concluída; e o lote tem um teto de tempo total (``Limites.lote_tempo_max_s``).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .config import Limites
from .dinamico import montar_entradas
from .execucao import Cancelado, Execucao, ResultadoLista, chave_etapa
from .historico import Historico
from .models import BlockType, Passo
from .passos import definicao_efetiva
from .passos_simples import PassosSimples
from .sandbox import SandboxResult, corpo_max_do_lote
from .store import agora

# Com um item só não há o que repartir, e o contêiner aquecido do pool atende bem esse caso.
ITENS_MINIMOS = 2

ExecutarLista = Callable[[Execucao, list[Passo], tuple[int, ...]], ResultadoLista]


@dataclass
class ResultadoDoLote:
    falhas: list[dict[str, Any]]  # as falhas não tratadas da iteração que falhou (vazio se todas deram certo)
    item_que_falhou: int | None   # posição (a partir de 0) dessa iteração


class LoteDePython:
    def __init__(self, simples: PassosSimples, historico: Historico, limites: Limites) -> None:
        self.simples = simples
        self.historico = historico
        self.limites = limites

    def rodar(self, ex: Execucao, laco: Passo, corpo: list[Passo], itens: list, iteracao: tuple[int, ...],
              executar_lista: ExecutarLista) -> ResultadoDoLote | None:
        """Roda o corpo do laço para todos os ``itens`` em lote, ou devolve None se ele não pode (ou não vale a pena) ser em lote.

        ``executar_lista`` é a execução normal de uma lista de passos: cada iteração passa por ela. Levanta Cancelado se o
        usuário cancelar durante o lote (o contêiner já foi abatido)."""
        if len(itens) < ITENS_MINIMOS:
            return None
        achado = self._passo_python(ex, corpo)
        if achado is None or not self.simples.lote_disponivel():
            return None
        passo, tipo = achado
        entradas = self._entradas(ex, laco, passo, tipo, itens)
        if entradas is None:
            return None

        resultado = ResultadoDoLote([], None)
        marcas: dict[int, tuple[str, float]] = {}

        def marcar(i: int) -> None:
            """A iteração i já aparece como em andamento enquanto o contêiner trabalha, como no caminho normal."""
            marcas[i] = (agora(), time.monotonic())
            self.historico.gravar_etapa(ex, passo.id, (*iteracao, i), state="executando", started_at=marcas[i][0])

        def ao_item(i: int, r: SandboxResult) -> bool:
            ex.resultados_prontos[passo.id] = r
            ex.valores[laco.id] = {"item": itens[i], "indice": i}
            try:
                falhas = executar_lista(ex, corpo, (*iteracao, i)).falhas
            finally:
                ex.resultados_prontos.pop(passo.id, None)
            # O despacho mediu o tempo de repassar o resultado; o que a iteração levou de fato é o que o contêiner levou.
            # A primeira inclui a subida do contêiner, como incluía no caminho normal.
            duracao = int((time.monotonic() - marcas[0][1]) * 1000) if i == 0 else r.duration_ms
            self.historico.atualizar_etapa(ex.run_id, chave_etapa(passo.id, (*iteracao, i)), started_at=marcas[i][0], duration_ms=duracao)
            if falhas:
                resultado.falhas, resultado.item_que_falhou = falhas, i
                return False
            if i + 1 < len(itens):
                marcar(i + 1)
            return True

        ex.checar_cancelamento()
        ex.valores[laco.id] = {"item": itens[0], "indice": 0}
        marcar(0)
        if self.simples.executar_lote(ex, passo, tipo, entradas, ao_item) == "cancelado":
            raise Cancelado()
        return resultado

    # ------------------------------------------------------------------ o que pode ir em lote
    def _passo_python(self, ex: Execucao, corpo: list[Passo]) -> tuple[Passo, BlockType] | None:
        """O corpo é EXATAMENTE um passo Python simples (embutido ou bloco da biblioteca), com a configuração padrão?"""
        if len(corpo) != 1:
            return None
        passo = corpo[0]
        tipo = ex.defs.get(f"{passo.type}@{passo.version}")
        if tipo is None or tipo.slots or passo.slots or not (tipo.kind == "python" or tipo.id == "builtin.python"):
            return None
        if passo.settings.retry.count or passo.settings.timeout_s is not None or passo.run_after != ["sucesso"]:
            return None
        return passo, tipo

    def _entradas(self, ex: Execucao, laco: Passo, passo: Passo, tipo: BlockType, itens: list) -> list[dict[str, Any]] | None:
        """As entradas de cada iteração, resolvidas antes de rodar. None se alguma não resolve ou se juntas passam do teto."""
        if any(ref.step == passo.id for campo in passo.inputs.values() for ref in campo.referencias()):
            return None  # lê a saída da iteração anterior: as entradas dependem do que o contêiner acabou de devolver
        ef = definicao_efetiva(tipo, passo.params)
        teto = corpo_max_do_lote(self.limites)
        entradas: list[dict[str, Any]] = []
        total = 0
        try:
            for i, item in enumerate(itens):
                ex.valores[laco.id] = {"item": item, "indice": i}
                entradas.append(montar_entradas(ex, passo, ef, self.limites))
                total += len(json.dumps(entradas[-1], ensure_ascii=True))
                if total > teto:
                    return None
        except Exception:  # qualquer problema aqui reaparece, na iteração certa, pelo caminho normal
            return None
        return entradas
