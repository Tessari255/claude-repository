"""Controle de fluxo: a sequência de passos com “Executar após” e os blocos que têm outros passos dentro.

Daqui saem as decisões de *quais* passos rodam e *quantas vezes* (condição, para cada, repetir até, escopo). Rodar um
passo em si fica com quem foi injetado em ``executar_passo``, o que evita um ciclo entre este módulo e o despacho.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .config import Limites
from .dinamico import avaliar_regras, montar_entradas
from .errors import ErroBloco
from .execucao import Execucao, Resultado, ResultadoLista
from .historico import Historico
from .models import BlockType, Passo
from .validation import parametros_efetivos

ESTADOS_ROTULO = {"concluido": "teve sucesso", "falhou": "falhou", "ignorado": "foi ignorado", "expirou": "expirou"}

_QUANDO_RODA = {"sucesso": "tiver sucesso", "falhou": "falhar", "ignorado": "for ignorado", "expirou": "expirar"}

ExecutarPasso = Callable[[Execucao, Passo, tuple[int, ...]], Resultado]


def deve_rodar(p: Passo, anterior: Resultado) -> tuple[bool, str]:
    """O passo roda dado o que aconteceu com o anterior? Devolve também a situação do anterior (para a mensagem)."""
    situacao = "expirou" if anterior.estado == "falhou" and anterior.expirou else anterior.estado
    permitido = {"sucesso": "concluido", "ignorado": "ignorado"}
    for cond in p.run_after:
        if cond == "falhou" and anterior.estado == "falhou" and not anterior.expirou:
            return True, situacao
        if cond == "expirou" and anterior.estado == "falhou" and anterior.expirou:
            return True, situacao
        if cond in permitido and anterior.estado == permitido[cond]:
            return True, situacao
    return False, situacao


def motivo_ignorado(p: Passo, anterior_nome: str, situacao: str) -> str:
    quando = " ou ".join(_QUANDO_RODA[c] for c in p.run_after)
    return (f"Não executado: o passo anterior, “{anterior_nome}”, {ESTADOS_ROTULO[situacao]}, "
            f"e este passo só roda se ele {quando}.")


class Controle:
    def __init__(self, historico: Historico, limites: Limites, executar_passo: ExecutarPasso) -> None:
        self.historico = historico
        self.limites = limites
        self.executar_passo = executar_passo

    # ----------------------------------------------------------------- sequência
    def lista(self, ex: Execucao, passos: list[Passo], iteracao: tuple[int, ...]) -> ResultadoLista:
        """Executa uma lista de passos em sequência. Devolve as falhas não tratadas e o resumo de cada passo."""
        anterior: Resultado | None = None
        anterior_nome = ""
        pendentes: list[dict[str, Any]] = []
        da_anterior: list[dict[str, Any]] = []
        resumo: list[dict[str, Any]] = []
        for p in passos:
            ex.checar_cancelamento()
            nome = ex.nomes.get(p.id, p.id)
            if anterior is not None:
                ok, situacao = deve_rodar(p, anterior)
                if not ok:
                    self.ignorar(ex, p, iteracao, motivo_ignorado(p, anterior_nome, situacao))
                    anterior, anterior_nome, da_anterior = Resultado("ignorado"), nome, []
                    resumo.append({"passo": nome, "estado": "ignorado", "erro": None})
                    continue
                if anterior.estado == "falhou":  # este passo trata a falha do anterior
                    for f in da_anterior:
                        if f in pendentes:
                            pendentes.remove(f)
            r = self.executar_passo(ex, p, iteracao)
            if r.estado == "falhou":
                pendentes.extend(r.falhas)
            anterior, anterior_nome, da_anterior = r, nome, list(r.falhas) if r.estado == "falhou" else []
            resumo.append({"passo": nome, "estado": r.estado, "erro": r.mensagem or None})
        return ResultadoLista(pendentes, resumo)

    def ignorar(self, ex: Execucao, passo: Passo, iteracao: tuple[int, ...], motivo: str) -> None:
        """Marca o passo e tudo o que há dentro dele como ignorado."""
        self.historico.gravar_etapa(ex, passo.id, iteracao, state="ignorado", skip_reason=motivo)
        nome = ex.nomes.get(passo.id, passo.id)
        for filhos in passo.slots.values():
            for f in filhos:
                self.ignorar(ex, f, iteracao, f"O bloco “{nome}” não foi executado.")

    # --------------------------------------------------------------- contêineres
    def conteiner(self, ex: Execucao, passo: Passo, tipo: BlockType,
                  iteracao: tuple[int, ...]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, str]]]:
        """Executa condição, para cada, repetir até ou escopo. Devolve (saídas, falhas não tratadas, logs)."""
        logs: list[dict[str, str]] = []
        params = parametros_efetivos(tipo, passo.params)
        entradas = montar_entradas(ex, passo, tipo, self.limites)
        self.historico.gravar_etapa(ex, passo.id, iteracao, inputs=entradas)

        if tipo.id == "builtin.condicao":
            saidas, falhas = self._condicao(ex, passo, params, iteracao, logs)
        elif tipo.id == "builtin.escopo":
            saidas, falhas = self._escopo(ex, passo, iteracao)
        elif tipo.id == "builtin.para_cada":
            saidas, falhas = self._para_cada(ex, passo, params, entradas["lista"], iteracao, logs)
        else:
            saidas, falhas = self._repetir_ate(ex, passo, params, iteracao)
        return saidas, falhas, logs

    def _condicao(self, ex: Execucao, passo: Passo, params: dict[str, Any], iteracao: tuple[int, ...],
                  logs: list[dict[str, str]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        nome = ex.nomes.get(passo.id, passo.id)
        r = avaliar_regras(ex, params, self.limites)
        logs.append({"source": "system", "text": f"Teste concluído: {'sim' if r else 'não'}. Seguindo por “{'Se sim' if r else 'Se não'}”."})
        escolhido, outro = ("sim", "nao") if r else ("nao", "sim")
        for f in passo.slots.get(outro, []):
            self.ignorar(ex, f, iteracao, f"O caminho “{'Se sim' if outro == 'sim' else 'Se não'}” da condição “{nome}” não foi escolhido.")
        ex.valores[passo.id] = {"resultado": r}
        res = self.lista(ex, passo.slots.get(escolhido, []), iteracao)
        return {"resultado": r}, res.falhas

    def _escopo(self, ex: Execucao, passo: Passo, iteracao: tuple[int, ...]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        ex.valores[passo.id] = {}
        res = self.lista(ex, passo.slots.get("corpo", []), iteracao)
        saidas = {"falhou": bool(res.falhas), "erro": res.falhas[0]["message"] if res.falhas else "",
                  "resultados": res.resumo}
        ex.valores[passo.id] = saidas
        return saidas, res.falhas

    def _para_cada(self, ex: Execucao, passo: Passo, params: dict[str, Any], lista: list, iteracao: tuple[int, ...],
                   logs: list[dict[str, str]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        nome = ex.nomes.get(passo.id, passo.id)
        corpo = passo.slots.get("corpo", [])
        limite = int(params.get("limite", 100))
        if limite > self.limites.itens_max_lista:
            raise ErroBloco(f"O limite de itens ({limite}) passa do máximo permitido ({self.limites.itens_max_lista}).",
                            codigo="parametro_invalido")
        if len(lista) > limite:
            raise ErroBloco(f"A lista tem {len(lista)} itens, mas o limite configurado é {limite}.", codigo="limite_itens",
                            sugestao=f"Aumente o “Limite de itens” no bloco (até {self.limites.itens_max_lista}) ou envie uma lista menor.")
        if not lista:
            logs.append({"source": "system", "text": "A lista está vazia: nenhum item foi percorrido."})
            for f in corpo:
                self.ignorar(ex, f, iteracao, f"A lista de “{nome}” está vazia.")
        falhas: list[dict[str, Any]] = []
        for i, item in enumerate(lista):
            ex.checar_cancelamento()
            ex.valores[passo.id] = {"item": item, "indice": i}
            falhas = self.lista(ex, corpo, (*iteracao, i)).falhas
            if falhas:
                logs.append({"source": "system", "text": f"O item {i + 1} de {len(lista)} falhou; as repetições seguintes foram canceladas."})
                break
        saidas = {"quantidade": len(lista)}
        ex.valores[passo.id] = saidas
        return saidas, falhas

    def _repetir_ate(self, ex: Execucao, passo: Passo, params: dict[str, Any],
                     iteracao: tuple[int, ...]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        corpo = passo.slots.get("corpo", [])
        limite = int(params.get("limite", 10))
        repeticoes, falhas = 0, []
        while True:
            ex.checar_cancelamento()
            ex.valores[passo.id] = {"indice": repeticoes}
            falhas = self.lista(ex, corpo, (*iteracao, repeticoes)).falhas
            repeticoes += 1
            if falhas or avaliar_regras(ex, params, self.limites):
                break
            if repeticoes >= limite:
                raise ErroBloco(f"A condição não ficou verdadeira em {limite} repetições.", codigo="limite_repeticoes",
                                sugestao="Confira a condição ou aumente o “Limite de repetições” (até 100).")
        saidas = {"repeticoes": repeticoes}
        ex.valores[passo.id] = saidas
        return saidas, falhas
