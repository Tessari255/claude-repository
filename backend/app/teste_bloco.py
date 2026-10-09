"""Teste isolado de um bloco: roda um único bloco com dados de exemplo e registra a execução (kind: bloco).

É o que o editor de blocos usa para o botão de testar. Passa pelos mesmos passos simples e pelo mesmo histórico de
uma execução de fluxo, então o isolamento do código Python e as conferências das saídas valem igualmente aqui.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .blocks.builtin import ContextoBloco
from .config import Limites
from .errors import ApiError, ErroBloco
from .execucao import Execucao, falha_de
from .historico import Historico
from .models import BlockType, Flow, Passo
from .passos import definicao_efetiva
from .passos_simples import PassosSimples
from .store import Store, agora
from .tipos import descrever_valor, rotulo_tipo, valor_e_do_tipo
from .validation import mensagem_parametro, parametro_visivel, parametros_efetivos, valor_efetivo

log = logging.getLogger("trama.motor")


def conferir_exemplo(tipo: BlockType, params: dict[str, Any], entradas: dict[str, Any]) -> None:
    """Os parâmetros e os valores de exemplo servem para testar o bloco? Junta todos os problemas numa resposta só."""
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


class TesteDeBloco:
    def __init__(self, store: Store, historico: Historico, simples: PassosSimples, limites: Limites) -> None:
        self.store = store
        self.historico = historico
        self.simples = simples
        self.limites = limites

    def testar(self, tipo: BlockType, params: dict[str, Any], entradas: dict[str, Any],
               project_id: str | None = None) -> dict[str, Any]:
        """Executa um único bloco com dados de exemplo e registra a execução (teste do editor de blocos)."""
        conferir_exemplo(tipo, params, entradas)

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
