"""Despacho de um passo: registra o andamento no histórico, escolhe quem o executa e traduz o desfecho em Resultado.

Contêineres (condição, laços, escopo) vão para o controle de fluxo; os demais, para os passos simples. É aqui que
uma exceção vira o estado do passo no histórico: falha prevista (ErroBloco), erro interno, cancelamento e Encerrar.
"""

from __future__ import annotations

import logging
import time

from .blocks.builtin import ContextoBloco
from .config import Limites
from .controle import Controle
from .errors import ErroBloco
from .execucao import Cancelado, Encerrado, Execucao, Resultado, falha_de
from .historico import Historico
from .models import Passo
from .passos_simples import PassosSimples
from .sandbox import DockerExecutor
from .store import agora

log = logging.getLogger("trama.motor")


class Despacho:
    def __init__(self, historico: Historico, executor: DockerExecutor, limites: Limites) -> None:
        self.historico = historico
        self.limites = limites
        self.simples = PassosSimples(historico, executor, limites)
        self.controle = Controle(historico, limites, self.passo)

    def passo(self, ex: Execucao, passo: Passo, iteracao: tuple[int, ...]) -> Resultado:
        tipo = ex.defs[f"{passo.type}@{passo.version}"]
        nome = ex.nomes.get(passo.id, passo.id)
        t0 = time.monotonic()
        ctx = ContextoBloco(limites=self.limites, dados_gatilho=ex.trigger_inputs)
        self.historico.gravar_etapa(ex, passo.id, iteracao, state="executando", started_at=agora())
        try:
            if tipo.slots:
                saidas, falhas, extra_logs = self.controle.conteiner(ex, passo, tipo, iteracao)
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
