"""Motor de execução de fluxos em passos.

* A lógica de execução vive aqui, no backend; o frontend só pede e acompanha.
* Os passos rodam **em sequência**, na ordem em que aparecem. Cada passo olha o estado do passo anterior
  da mesma lista e decide se roda (“Executar após”, como no Power Automate). O padrão é rodar só se o
  anterior teve sucesso; por isso uma falha faz os passos seguintes serem *ignorados*, a menos que algum
  deles seja configurado para rodar após a falha (o “capturar erro” de um escopo).
* Cada execução usa um *snapshot* congelado do fluxo e das definições de bloco (com a versão fixada em
  cada passo), então editar um bloco depois não altera execuções nem fluxos existentes.
* Blocos internos rodam aqui (código nosso); código do usuário roda SOMENTE no executor isolado.
* Uma falha que ninguém trata (nenhum passo posterior roda após ela) deixa a execução como *falhou*;
  uma falha tratada deixa a execução como *concluída*, como no Power Automate.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .blocks.builtin import (
    ContextoBloco,
)
from .config import Limites
from .dinamico import avaliar_regras, montar_entradas
from .errors import ApiError, ErroBloco
from .execucao import (
    MAX_REGISTROS,
    Cancelado,
    Encerrado,
    Execucao,
    Resultado,
    ResultadoLista,
    falha_de,
)
from .historico import Historico
from .models import BlockType, Flow, Passo
from .passos import definicao_efetiva, percorrer
from .passos_simples import PassosSimples
from .registry import Registro
from .sandbox import DockerExecutor
from .store import Store, agora
from .tipos import descrever_valor, rotulo_tipo, valor_e_do_tipo
from .validation import (
    CONTEINERES_DE_LACO,
    analisar,
    mensagem_parametro,
    nome_passo,
    parametro_visivel,
    parametros_efetivos,
    valor_efetivo,
)

log = logging.getLogger("trama.motor")

ESTADOS_ROTULO = {"concluido": "teve sucesso", "falhou": "falhou", "ignorado": "foi ignorado", "expirou": "expirou"}


class Motor:
    def __init__(self, store: Store, executor: DockerExecutor, registro: Registro, limites: Limites,
                 workers: int = 4) -> None:
        from concurrent.futures import ThreadPoolExecutor

        self.store = store
        self.historico = Historico(store)
        self.executor = executor
        self.registro = registro
        self.limites = limites
        self.simples = PassosSimples(self.historico, executor, limites)
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="trama-exec")
        self._cancelamentos: dict[str, threading.Event] = {}
        self._trava = threading.Lock()

    def encerrar(self) -> None:
        with self._trava:
            for ev in self._cancelamentos.values():
                ev.set()
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ---------------------------------------------------------------- preparação
    def preparar(self, flow: Flow, project_id: str | None, dados_gatilho: dict[str, Any] | None = None) -> str:
        """Valida TUDO antes de executar e cria o registro da execução (estado: aguardando)."""
        analise = analisar(flow, self.registro.resolver, sandbox=self.executor.status(), ultima_versao=None,
                           limites=self.limites)
        if analise.erros:
            raise ApiError(422, "fluxo_invalido",
                           "O fluxo tem problemas e não foi executado. Corrija os itens abaixo e tente de novo.",
                           problemas=[i.model_dump() for i in analise.erros])
        dados = self._conferir_dados_do_gatilho(flow, analise.efetivas[flow.trigger.id], dados_gatilho or {})
        passos = [flow.trigger] + [pos.passo for pos in percorrer(flow.steps)]
        em_laco = {pos.passo.id for pos in percorrer(flow.steps)
                   if any(analise.defs[a].id in CONTEINERES_DE_LACO for a in pos.ancestrais if a in analise.defs)}
        definicoes = {f"{p.type}@{p.version}": analise.defs[p.id].model_dump() for p in passos}
        snapshot = {"flow": flow.model_dump(), "definitions": definicoes, "port_types": analise.port_types,
                    "trigger_inputs": dados}
        return self.historico.criar_execucao(kind="fluxo", project_id=project_id, snapshot=snapshot,
                                             etapas=[p.id for p in passos if p.id not in em_laco], trigger_inputs=dados)

    @staticmethod
    def _conferir_dados_do_gatilho(flow: Flow, ef: BlockType, dados: dict[str, Any]) -> dict[str, Any]:
        problemas: list[dict[str, Any]] = []
        campos = {o.id: o for o in ef.outputs}
        for chave, valor in dados.items():
            c = campos.get(chave)
            if c is None:
                problemas.append({"code": "dados_invalidos", "severity": "erro", "scope": "configuracao",
                                  "message": f"O gatilho não tem o campo “{chave}”.", "field": chave})
            elif not valor_e_do_tipo(valor, c.type):
                problemas.append({"code": "dados_invalidos", "severity": "erro", "scope": "configuracao",
                                  "message": f"O campo “{c.label}” deveria ser {rotulo_tipo(c.type)}, mas recebeu {descrever_valor(valor)}.",
                                  "field": chave})
        for c in ef.outputs:
            if c.required and c.default is None and c.id not in dados:
                problemas.append({"code": "dados_invalidos", "severity": "erro", "scope": "configuracao",
                                  "message": f"Preencha o campo “{c.label}” do gatilho.", "field": c.id})
        if problemas:
            raise ApiError(422, "dados_invalidos", "Os dados informados ao gatilho não são válidos.", problemas=problemas,
                           sugestao="Confira os campos do gatilho e tente de novo.")
        return dados

    def despachar(self, run_id: str) -> None:
        with self._trava:
            self._cancelamentos[run_id] = threading.Event()
        self._pool.submit(self.rodar, run_id)

    def cancelar(self, run_id: str) -> bool:
        """Pede o cancelamento. O passo em andamento termina (ou estoura o limite) e o resto é abandonado."""
        with self._trava:
            ev = self._cancelamentos.get(run_id)
        if ev is None:
            return False
        ev.set()
        return True

    # ----------------------------------------------------------------- execução
    def rodar(self, run_id: str) -> None:
        """Executa a execução `run_id` do início ao fim (síncrono)."""
        with self._trava:
            ev = self._cancelamentos.setdefault(run_id, threading.Event())
        try:
            self._rodar(run_id, ev)
        except Exception:
            log.exception("Falha inesperada no motor (execução %s)", run_id)
            self.historico.atualizar_execucao(
                run_id, state="falhou", finished_at=agora(),
                error={"code": "erro_interno", "message": "Ocorreu um erro interno ao executar o fluxo.",
                       "suggestion": "Tente novamente. Se persistir, consulte o log do servidor.", "technical": None})
            self.historico.encerrar_etapas_abertas(run_id, "A execução terminou com um erro interno.")
        finally:
            with self._trava:
                self._cancelamentos.pop(run_id, None)

    def _rodar(self, run_id: str, cancelar: threading.Event) -> None:
        run = self.store.obter_execucao(run_id, com_snapshot=True)
        assert run is not None
        snap = run["snapshot"]
        flow = Flow.model_validate(snap["flow"])
        defs = {k: BlockType.model_validate(v) for k, v in snap["definitions"].items()}
        ex = Execucao(run_id=run_id, flow=flow, defs=defs, port_types=snap["port_types"],
                      trigger_inputs=snap["trigger_inputs"], cancelar=cancelar,
                      posicao=self.store.contar_etapas(run_id), max_registros=MAX_REGISTROS)
        for pos in percorrer(flow.steps):
            ex.nomes[pos.passo.id] = nome_passo(pos.passo, defs.get(f"{pos.passo.type}@{pos.passo.version}"))
        ex.nomes[flow.trigger.id] = nome_passo(flow.trigger, defs.get(f"{flow.trigger.type}@{flow.trigger.version}"))

        inicio = time.monotonic()
        self.historico.atualizar_execucao(run_id, state="executando", started_at=agora())
        estado, erro, mensagem = "concluido", None, None
        try:
            ex.checar_cancelamento()
            r = self._passo(ex, flow.trigger, ())
            falhas = list(r.falhas)
            if r.estado != "falhou":
                falhas = self._lista(ex, flow.steps, ()).falhas
            else:
                for p in flow.steps:
                    self._ignorar(ex, p, (), "O gatilho falhou, então o fluxo não seguiu.")
            if falhas:
                estado, erro = "falhou", falhas[0]
        except Encerrado as e:
            estado = {"sucesso": "concluido", "falha": "falhou", "cancelado": "cancelado"}[e.estado]
            mensagem = e.mensagem or None
            if estado == "falhou":
                erro = {"step_id": e.passo.id, "step_name": ex.nomes.get(e.passo.id, e.passo.id),
                        "code": "encerrado_com_falha", "message": e.mensagem or "O fluxo foi encerrado com falha.",
                        "suggestion": None, "line": None, "technical": None}
            self.historico.encerrar_etapas_abertas(
                run_id, f"A execução foi encerrada pelo passo “{ex.nomes.get(e.passo.id, e.passo.id)}”.", estado_aberto="concluido")
        except Cancelado:
            estado, mensagem = "cancelado", "A execução foi cancelada."
            self.historico.encerrar_etapas_abertas(run_id, "A execução foi cancelada.", estado_aberto="cancelado")
        resultado: dict[str, Any] = {"outputs": ex.saidas_finais}
        if mensagem:
            resultado["message"] = mensagem
        self.historico.atualizar_execucao(
            run_id, state=estado, finished_at=agora(), duration_ms=int((time.monotonic() - inicio) * 1000),
            result=resultado, error=erro)

    # ----------------------------------------------------------- listas e passos
    def _lista(self, ex: Execucao, passos: list[Passo], iteracao: tuple[int, ...]) -> ResultadoLista:
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
                ok, situacao = self._deve_rodar(p, anterior)
                if not ok:
                    quando = " ou ".join({"sucesso": "tiver sucesso", "falhou": "falhar", "ignorado": "for ignorado",
                                          "expirou": "expirar"}[c] for c in p.run_after)
                    motivo = (f"Não executado: o passo anterior, “{anterior_nome}”, {ESTADOS_ROTULO[situacao]}, "
                              f"e este passo só roda se ele {quando}.")
                    self._ignorar(ex, p, iteracao, motivo)
                    anterior, anterior_nome, da_anterior = Resultado("ignorado"), nome, []
                    resumo.append({"passo": nome, "estado": "ignorado", "erro": None})
                    continue
                if anterior.estado == "falhou":  # este passo trata a falha do anterior
                    for f in da_anterior:
                        if f in pendentes:
                            pendentes.remove(f)
            r = self._passo(ex, p, iteracao)
            if r.estado == "falhou":
                pendentes.extend(r.falhas)
            anterior, anterior_nome, da_anterior = r, nome, list(r.falhas) if r.estado == "falhou" else []
            resumo.append({"passo": nome, "estado": r.estado, "erro": r.mensagem or None})
        return ResultadoLista(pendentes, resumo)

    @staticmethod
    def _deve_rodar(p: Passo, anterior: Resultado) -> tuple[bool, str]:
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

    def _ignorar(self, ex: Execucao, passo: Passo, iteracao: tuple[int, ...], motivo: str) -> None:
        """Marca o passo e tudo o que há dentro dele como ignorado."""
        self.historico.gravar_etapa(ex, passo.id, iteracao, state="ignorado", skip_reason=motivo)
        nome = ex.nomes.get(passo.id, passo.id)
        for filhos in passo.slots.values():
            for f in filhos:
                self._ignorar(ex, f, iteracao, f"O bloco “{nome}” não foi executado.")

    # ------------------------------------------------------------------ um passo
    def _tipo(self, ex: Execucao, passo: Passo) -> BlockType:
        return ex.defs[f"{passo.type}@{passo.version}"]

    def _passo(self, ex: Execucao, passo: Passo, iteracao: tuple[int, ...]) -> Resultado:
        tipo = self._tipo(ex, passo)
        nome = ex.nomes.get(passo.id, passo.id)
        t0 = time.monotonic()
        ctx = ContextoBloco(limites=self.limites, dados_gatilho=ex.trigger_inputs)
        self.historico.gravar_etapa(ex, passo.id, iteracao, state="executando", started_at=agora())
        try:
            if tipo.slots:
                saidas, falhas, extra_logs = self._conteiner(ex, passo, tipo, iteracao, ctx)
                ctx.logs.extend(extra_logs)
                dur = int((time.monotonic() - t0) * 1000)
                if falhas:
                    erro = ErroBloco(
                        f"O passo “{falhas[0]['step_name']}” dentro deste bloco falhou: {falhas[0]['message']}",
                        codigo="falha_em_passo_interno")
                    self.historico.gravar_etapa(ex, passo.id, iteracao, state="falhou", finished_at=agora(), duration_ms=dur,
                                                outputs=saidas, logs=ctx.logs, error=erro.como_dict())
                    return Resultado("falhou", False, falhas, falhas[0]["message"])
                self.historico.gravar_etapa(ex, passo.id, iteracao, state="concluido", finished_at=agora(), duration_ms=dur,
                                            outputs=saidas, logs=ctx.logs)
                return Resultado("concluido")
            saidas = self.simples.executar_folha(ex, passo, tipo, iteracao, ctx)
            self.historico.gravar_etapa(ex, passo.id, iteracao, state="concluido", finished_at=agora(),
                                        duration_ms=int((time.monotonic() - t0) * 1000), outputs=saidas, logs=ctx.logs)
            return Resultado("concluido")
        except (Cancelado, Encerrado) as e:
            estado = "cancelado" if isinstance(e, Cancelado) or (isinstance(e, Encerrado) and e.estado == "cancelado") else "concluido"
            self.historico.gravar_etapa(ex, passo.id, iteracao, state=estado, finished_at=agora(),
                                        duration_ms=int((time.monotonic() - t0) * 1000), logs=ctx.logs)
            raise
        except ErroBloco as e:
            dur = int((time.monotonic() - t0) * 1000)
            self.historico.gravar_etapa(ex, passo.id, iteracao, state="falhou", finished_at=agora(), duration_ms=dur,
                                        logs=ctx.logs, error=e.como_dict())
            return Resultado("falhou", e.codigo == "tempo_esgotado", [falha_de(passo.id, nome, e)], e.mensagem)
        except Exception as e:
            log.exception("Erro inesperado no passo %s", passo.id)
            erro = ErroBloco("Ocorreu um erro interno ao executar este passo.", codigo="erro_interno",
                             tecnico={"type": type(e).__name__})
            self.historico.gravar_etapa(ex, passo.id, iteracao, state="falhou", finished_at=agora(),
                                        duration_ms=int((time.monotonic() - t0) * 1000), logs=ctx.logs, error=erro.como_dict())
            return Resultado("falhou", False, [falha_de(passo.id, nome, erro)], erro.mensagem)

    # ------------------------------------------------------------------ contêineres
    def _conteiner(self, ex: Execucao, passo: Passo, tipo: BlockType, iteracao: tuple[int, ...],
                   ctx: ContextoBloco) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, str]]]:
        """Executa condição, para cada, repetir até ou escopo. Devolve (saídas, falhas não tratadas, logs)."""
        logs: list[dict[str, str]] = []
        nome = ex.nomes.get(passo.id, passo.id)
        params = parametros_efetivos(tipo, passo.params)
        entradas = montar_entradas(ex, passo, tipo, self.limites)
        self.historico.gravar_etapa(ex, passo.id, iteracao, inputs=entradas)

        if tipo.id == "builtin.condicao":
            r = avaliar_regras(ex, params, self.limites)
            logs.append({"source": "system", "text": f"Teste concluído: {'sim' if r else 'não'}. Seguindo por “{'Se sim' if r else 'Se não'}”."})
            escolhido, outro = ("sim", "nao") if r else ("nao", "sim")
            for f in passo.slots.get(outro, []):
                self._ignorar(ex, f, iteracao, f"O caminho “{'Se sim' if outro == 'sim' else 'Se não'}” da condição “{nome}” não foi escolhido.")
            ex.valores[passo.id] = {"resultado": r}
            res = self._lista(ex, passo.slots.get(escolhido, []), iteracao)
            return {"resultado": r}, res.falhas, logs

        if tipo.id == "builtin.escopo":
            ex.valores[passo.id] = {}
            res = self._lista(ex, passo.slots.get("corpo", []), iteracao)
            saidas = {"falhou": bool(res.falhas), "erro": res.falhas[0]["message"] if res.falhas else "",
                      "resultados": res.resumo}
            ex.valores[passo.id] = saidas
            return saidas, res.falhas, logs

        corpo = passo.slots.get("corpo", [])
        if tipo.id == "builtin.para_cada":
            lista = entradas["lista"]
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
                    self._ignorar(ex, f, iteracao, f"A lista de “{nome}” está vazia.")
            falhas: list[dict[str, Any]] = []
            for i, item in enumerate(lista):
                ex.checar_cancelamento()
                ex.valores[passo.id] = {"item": item, "indice": i}
                falhas = self._lista(ex, corpo, (*iteracao, i)).falhas
                if falhas:
                    logs.append({"source": "system", "text": f"O item {i + 1} de {len(lista)} falhou; as repetições seguintes foram canceladas."})
                    break
            saidas = {"quantidade": len(lista)}
            ex.valores[passo.id] = saidas
            return saidas, falhas, logs

        # repetir até
        limite = int(params.get("limite", 10))
        repeticoes, falhas = 0, []
        while True:
            ex.checar_cancelamento()
            ex.valores[passo.id] = {"indice": repeticoes}
            falhas = self._lista(ex, corpo, (*iteracao, repeticoes)).falhas
            repeticoes += 1
            if falhas or avaliar_regras(ex, params, self.limites):
                break
            if repeticoes >= limite:
                raise ErroBloco(f"A condição não ficou verdadeira em {limite} repetições.", codigo="limite_repeticoes",
                                sugestao="Confira a condição ou aumente o “Limite de repetições” (até 100).")
        saidas = {"repeticoes": repeticoes}
        ex.valores[passo.id] = saidas
        return saidas, falhas, logs

    # --------------------------------------------------------------- teste isolado
    def testar_bloco(self, tipo: BlockType, params: dict[str, Any], entradas: dict[str, Any],
                     project_id: str | None = None) -> dict[str, Any]:
        """Executa um único bloco com dados de exemplo e registra a execução (teste do editor de blocos)."""
        problemas: list[dict[str, Any]] = []
        completos = {p.id: valor_efetivo(p, params) for p in tipo.params}
        for p in tipo.params:
            if parametro_visivel(p, tipo, completos):
                msg = mensagem_parametro(p, completos[p.id])
                if msg:
                    problemas.append({"code": "parametro_invalido", "severity": "erro", "scope": "configuracao",
                                      "message": msg, "field": p.id})
        for porta in tipo.inputs:
            if porta.required and porta.id not in entradas:
                problemas.append({"code": "entrada_obrigatoria", "severity": "erro", "scope": "configuracao",
                                  "message": f"Informe um valor de exemplo para a entrada “{porta.label}”.", "field": porta.id})
            elif porta.id in entradas and not valor_e_do_tipo(entradas[porta.id], porta.type):
                problemas.append({"code": "tipo_incompativel", "severity": "erro", "scope": "configuracao",
                                  "message": f"O exemplo da entrada “{porta.label}” deveria ser {rotulo_tipo(porta.type)}, "
                                             f"mas é {descrever_valor(entradas[porta.id])}.", "field": porta.id})
        for d in sorted(set(entradas) - {p.id for p in tipo.inputs}):
            problemas.append({"code": "entrada_desconhecida", "severity": "erro", "scope": "configuracao",
                              "message": f"“{d}” não é uma entrada deste bloco."})
        if problemas:
            raise ApiError(422, "teste_invalido", "Corrija os dados de exemplo antes de testar o bloco.", problemas=problemas)

        passo = Passo(id="teste", type=tipo.id, version=tipo.version, params=params)
        flow = Flow(steps=[passo])
        tipos_porta = {"teste": {"inputs": {p.id: p.type for p in tipo.inputs}, "outputs": {p.id: p.type for p in tipo.outputs}}}
        snapshot = {"flow": flow.model_dump(), "definitions": {f"{tipo.id}@{tipo.version}": tipo.model_dump()},
                    "port_types": tipos_porta, "trigger_inputs": {}, "test_inputs": entradas}
        rid = self.historico.criar_execucao(kind="bloco", project_id=project_id, snapshot=snapshot, etapas=["teste"])
        self._rodar_teste(rid, passo, tipo, entradas, tipos_porta["teste"]["outputs"], params)
        return self.store.obter_execucao(rid)  # type: ignore[return-value]

    def _rodar_teste(self, rid: str, passo: Passo, tipo: BlockType, entradas: dict[str, Any],
                     tipos_saida: dict[str, str], params: dict[str, Any]) -> None:
        t0 = time.monotonic()
        self.historico.atualizar_execucao(rid, state="executando", started_at=agora())
        self.historico.atualizar_etapa(rid, "teste", state="executando", started_at=agora(), inputs=entradas)
        ctx = ContextoBloco(limites=self.limites)
        ex = Execucao(run_id=rid, flow=Flow(steps=[passo]), defs={}, port_types={"teste": {"inputs": {}, "outputs": tipos_saida}},
                      trigger_inputs={}, cancelar=threading.Event())
        try:
            ef = definicao_efetiva(tipo, params)
            saidas = self.simples.executar(ex, passo, tipo, ef, entradas, parametros_efetivos(tipo, params), ctx)
        except ErroBloco as e:
            dur = int((time.monotonic() - t0) * 1000)
            self.historico.atualizar_etapa(rid, "teste", state="falhou", finished_at=agora(), duration_ms=dur,
                                           logs=ctx.logs, error=e.como_dict())
            self.historico.atualizar_execucao(rid, state="falhou", finished_at=agora(), duration_ms=dur, result={"outputs": []},
                                              error=falha_de("teste", tipo.name, e))
            return
        except Exception as e:
            log.exception("Erro inesperado ao testar bloco %s", tipo.id)
            dur = int((time.monotonic() - t0) * 1000)
            erro = ErroBloco("Ocorreu um erro interno ao executar este bloco.", codigo="erro_interno",
                             tecnico={"type": type(e).__name__})
            self.historico.atualizar_etapa(rid, "teste", state="falhou", finished_at=agora(), duration_ms=dur, error=erro.como_dict())
            self.historico.atualizar_execucao(rid, state="falhou", finished_at=agora(), duration_ms=dur,
                                              error=falha_de("teste", tipo.name, erro))
            return
        dur = int((time.monotonic() - t0) * 1000)
        self.historico.atualizar_etapa(rid, "teste", state="concluido", finished_at=agora(), duration_ms=dur,
                                       outputs=saidas, logs=ctx.logs)
        self.historico.atualizar_execucao(rid, state="concluido", finished_at=agora(), duration_ms=dur,
                                          result={"outputs": [{"step_id": "teste", "title": tipo.name, "value": saidas}]})
