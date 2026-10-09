"""Execução dos passos que não têm outros passos dentro: blocos internos, variáveis, Encerrar e código Python.

Blocos internos rodam aqui (código nosso); código do usuário roda SOMENTE no executor isolado. A resposta do
executor é tratada como não confiável: antes de virar saída ela é conferida contra o contrato declarado do bloco
(chaves, tipos, JSON puro e tamanho).
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from .blocks.builtin import HANDLERS, ContextoBloco, requer_sandbox, valor_padrao_do_tipo
from .config import Limites
from .dinamico import montar_entradas
from .errors import ErroBloco
from .erros_sandbox import NAO_REPETIR, SUGESTOES, erro_da_sandbox
from .execucao import Cancelado, Encerrado, Execucao
from .historico import Historico
from .models import BlockType, Passo
from .passos import definicao_efetiva
from .sandbox import AoItem, Desfecho, DockerExecutor
from .tipos import descrever_valor, rotulo_tipo, validar_json_puro, valor_e_do_tipo
from .validation import parametros_efetivos


class PassosSimples:
    def __init__(self, historico: Historico, executor: DockerExecutor, limites: Limites) -> None:
        self.historico = historico
        self.executor = executor
        self.limites = limites

    def executar_folha(self, ex: Execucao, passo: Passo, tipo: BlockType, iteracao: tuple[int, ...], ctx: ContextoBloco) -> dict[str, Any]:
        ef = definicao_efetiva(tipo, passo.params)
        entradas = montar_entradas(ex, passo, ef, self.limites)
        self.historico.gravar_etapa(ex, passo.id, iteracao, inputs=entradas)
        params = parametros_efetivos(tipo, passo.params)
        tentativas = 1 + passo.settings.retry.count
        saidas: dict[str, Any] = {}
        n = 0
        for n in range(1, tentativas + 1):
            ex.checar_cancelamento()
            try:
                saidas = self.executar(ex, passo, tipo, ef, entradas, params, ctx)
                break
            except ErroBloco as e:
                if n >= tentativas or e.codigo in NAO_REPETIR:
                    raise
                ctx.logs.append({"source": "system", "text": f"Tentativa {n} de {tentativas} falhou ({e.mensagem}). "
                                                              f"Nova tentativa em {passo.settings.retry.interval_s:g} s."})
                if ex.cancelar.wait(passo.settings.retry.interval_s):
                    raise Cancelado() from None
        if n > 1:
            ctx.logs.append({"source": "system", "text": f"Tentativa {n} de {tentativas} concluída com sucesso."})
        ex.valores[passo.id] = {**ex.valores.get(passo.id, {}), **saidas} if tipo.id.startswith("builtin.var_") else saidas
        if tipo.id == "builtin.saida":
            ex.saidas_finais.append({"step_id": passo.id, "title": passo.params.get("titulo", "Resultado") or ex.nomes[passo.id],
                                     "value": entradas["valor"]})
        return saidas

    def executar(self, ex: Execucao, passo: Passo, tipo: BlockType, ef: BlockType, entradas: dict[str, Any],
                  params: dict[str, Any], ctx: ContextoBloco) -> dict[str, Any]:
        """Executa um passo que não é contêiner. Levanta ErroBloco."""
        if tipo.id == "builtin.encerrar":
            mensagem = str(entradas.get("mensagem", ""))
            ctx.logs.append({"source": "system", "text": f"Encerrando o fluxo ({params.get('estado', 'falha')}): {mensagem or 'sem mensagem'}"})
            raise Encerrado(str(params.get("estado", "falha")), mensagem, passo)
        if tipo.id.startswith("builtin.var_"):
            return self._variavel(ex, passo, tipo, entradas, params)

        if requer_sandbox(tipo, params):
            status = self.executor.status()
            if not status.disponivel:
                raise ErroBloco(status.mensagem or "O executor isolado não está disponível.",
                                codigo="executor_indisponivel", sugestao=status.instrucao)
        limites = self.limites
        if passo.settings.timeout_s is not None:
            limites = dataclasses.replace(self.limites, tempo_s=min(passo.settings.timeout_s, self.limites.tempo_s))
        if tipo.kind == "python" or tipo.id == "builtin.python":
            codigo = ef.code if tipo.id == "builtin.python" else tipo.code
            r = ex.resultados_prontos.pop(passo.id, None)  # o lote do laço já rodou este item, junto com os outros
            if r is None:
                r = self.executor.run("block", codigo or "", entradas, {} if tipo.id == "builtin.python" else params, limits=limites)
            ctx.logs.extend(r.logs)
            if not r.ok:
                raise erro_da_sandbox(r.error or {})
            bruto = (r.payload or {}).get("outputs", {})
        else:
            def mapa(codigo: str, itens: list, p: dict) -> list:
                res = self.executor.run("map", codigo, items=itens, params=p, limits=limites)
                ctx.logs.extend(res.logs)
                if not res.ok:
                    raise erro_da_sandbox(res.error or {})
                return (res.payload or {}).get("items", [])

            ctx.mapa_python = mapa
            bruto = HANDLERS[tipo.id](entradas, params, ctx)
        tipos_saida = ex.port_types.get(passo.id, {}).get("outputs", {})
        return self._validar_saidas(ef, bruto, tipos_saida)

    def lote_disponivel(self) -> bool:
        """O executor isolado está de pé e roda um laço inteiro em um contêiner só?"""
        status = self.executor.status()
        return status.disponivel and status.lote

    def executar_lote(self, ex: Execucao, passo: Passo, tipo: BlockType, entradas: list[dict[str, Any]], ao_item: AoItem) -> Desfecho:
        """Roda o passo Python de um laço para todos os itens em UM contêiner. ``entradas`` traz as de cada item, em ordem.

        Cada resultado chega a ``ao_item`` assim que o item termina. Os limites são os do padrão, por item: um passo com
        tentativas ou tempo próprio nunca vem para cá."""
        ef = definicao_efetiva(tipo, passo.params)
        embutido = tipo.id == "builtin.python"
        codigo = ef.code if embutido else tipo.code
        params = {} if embutido else parametros_efetivos(tipo, passo.params)
        return self.executor.run_lote(codigo or "", entradas, params, self.limites, cancelar=ex.cancelar, ao_item=ao_item)

    def _variavel(self, ex: Execucao, passo: Passo, tipo: BlockType, entradas: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        if tipo.id == "builtin.var_inicializar":
            declarado = str(params.get("tipo", "texto"))
            valor = entradas["inicial"] if "inicial" in entradas else valor_padrao_do_tipo(declarado)
            if not valor_e_do_tipo(valor, declarado):
                raise ErroBloco(f"O valor inicial deveria ser {rotulo_tipo(declarado)}, mas é {descrever_valor(valor)}.",
                                codigo="entrada_invalida")
            ex.var_tipos[passo.id] = declarado
            ex.valores[passo.id] = {"valor": valor}
            return {"valor": valor}
        var_id = str(params.get("variavel", ""))
        if var_id not in ex.var_tipos:
            raise ErroBloco("A variável ainda não foi inicializada neste ponto do fluxo.", codigo="variavel_invalida",
                            sugestao="Coloque “Inicializar variável” antes deste passo, na lista principal do fluxo.")
        declarado = ex.var_tipos[var_id]
        atual = ex.valores[var_id]["valor"]
        if tipo.id == "builtin.var_definir":
            novo = entradas["valor"]
            if not valor_e_do_tipo(novo, declarado):
                raise ErroBloco(f"A variável é {rotulo_tipo(declarado)}, mas o novo valor é {descrever_valor(novo)}.",
                                codigo="entrada_invalida")
        elif tipo.id == "builtin.var_incrementar":
            soma = atual + entradas.get("quantidade", 1)
            if isinstance(soma, float) and (soma != soma or soma in (float("inf"), float("-inf"))):
                raise ErroBloco("O resultado não é um número válido.", codigo="resultado_invalido")
            novo = int(soma) if isinstance(soma, float) and soma.is_integer() and abs(soma) < 1e15 else soma
        else:  # acrescentar
            novo = [*atual, entradas["valor"]]
            if len(json.dumps(novo, ensure_ascii=False)) > self.limites.valor_max:
                raise ErroBloco(f"A lista ficaria grande demais (mais de {self.limites.valor_max // 1024} KB).",
                                codigo="valor_grande_demais")
        ex.valores[var_id] = {"valor": novo}
        return {"valor": novo}

    def _validar_saidas(self, tipo: BlockType, bruto: Any, tipos_saida: dict[str, str]) -> dict[str, Any]:
        """Confere o retorno contra o contrato declarado: chaves, tipos, JSON puro e tamanho."""
        if not isinstance(bruto, dict):
            raise ErroBloco("O bloco devolveu um resultado que não é um dicionário de saídas.",
                            codigo="retorno_invalido", sugestao=SUGESTOES["retorno_invalido"])
        declaradas = {p.id: p for p in tipo.outputs}
        extras = sorted(set(bruto) - set(declaradas))
        if extras:
            raise ErroBloco(
                f"O bloco devolveu saídas que não foram declaradas: {', '.join(extras)}.",
                codigo="retorno_invalido",
                sugestao="Declare essas saídas no bloco ou remova-as do `return`. "
                         f"Saídas declaradas: {', '.join(declaradas) or 'nenhuma'}.")
        saidas: dict[str, Any] = {}
        for pid, porta in declaradas.items():
            if pid not in bruto:
                raise ErroBloco(
                    f"O bloco não devolveu a saída declarada “{porta.label}” ({pid}).",
                    codigo="retorno_invalido",
                    sugestao=f"Inclua \"{pid}\" no dicionário devolvido por run(), por exemplo: return {{\"{pid}\": valor}}.")
            valor = bruto[pid]
            try:
                validar_json_puro(valor)
                texto = json.dumps(valor, ensure_ascii=False)
            except (ValueError, TypeError) as e:
                raise ErroBloco(f"A saída “{porta.label}” não pode ser convertida para JSON: {e}.",
                                codigo="retorno_invalido", sugestao=SUGESTOES["retorno_invalido"]) from None
            if len(texto.encode("utf-8")) > self.limites.valor_max:
                raise ErroBloco(
                    f"A saída “{porta.label}” é grande demais (limite de {self.limites.valor_max // 1024} KB).",
                    codigo="valor_grande_demais")
            esperado = tipos_saida.get(pid, porta.type)
            if not valor_e_do_tipo(valor, esperado):
                raise ErroBloco(
                    f"A saída “{porta.label}” deveria ser {rotulo_tipo(esperado)}, mas o bloco devolveu "
                    f"{descrever_valor(valor)}.",
                    codigo="retorno_invalido",
                    sugestao="Ajuste o código para devolver o tipo declarado ou mude o tipo da saída no editor do bloco.")
            saidas[pid] = json.loads(texto)
        return saidas
