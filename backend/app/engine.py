"""Motor de execução de fluxos em passos (fachada).

* A lógica de execução vive aqui, no backend; o frontend só pede e acompanha.
* Os passos rodam **em sequência**, na ordem em que aparecem. Cada passo olha o estado do passo anterior
  da mesma lista e decide se roda (“Executar após”, como no Power Automate). O padrão é rodar só se o
  anterior teve sucesso; por isso uma falha faz os passos seguintes serem *ignorados*, a menos que algum
  deles seja configurado para rodar após a falha (o “capturar erro” de um escopo).
* Cada execução usa um *snapshot* congelado do fluxo e das definições de bloco (com a versão fixada em
  cada passo), então editar um bloco depois não altera execuções nem fluxos existentes.
* Blocos internos rodam no processo da API (código nosso); código do usuário roda SOMENTE no executor isolado.
* Uma falha que ninguém trata (nenhum passo posterior roda após ela) deixa a execução como *falhou*;
  uma falha tratada deixa a execução como *concluída*, como no Power Automate.

O Motor só orquestra; cada responsabilidade mora em um módulo (todos abaixo dele, nenhum importa este):
``preparo`` (validar e congelar o snapshot), ``execucao`` (estado de uma execução), ``historico`` (única porta de
escrita do histórico), ``despacho`` (um passo: andamento, desfecho e erros), ``controle`` (sequência, condição, laços
e escopo), ``passos_simples`` (blocos internos, variáveis e a ida ao executor isolado), ``lote`` (um "Para cada" cujo corpo
é só um passo Python roda todas as iterações em um único contêiner), ``dinamico`` (conteúdo dinâmico e regras),
``erros_sandbox`` (tradução dos erros do executor) e ``teste_bloco`` (teste isolado de um bloco).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from pydantic import ValidationError

from .config import Limites
from .controle import ESTADOS_ROTULO
from .despacho import Despacho
from .errors import ApiError
from .erros_sandbox import NAO_REPETIR, erro_da_sandbox
from .execucao import MAX_REGISTROS, Cancelado, Encerrado, Execucao, Resultado, ResultadoLista, chave_etapa
from .historico import Historico
from .models import BlockType, Flow
from .passos import percorrer
from .preparo import preparar_execucao
from .registry import Registro
from .sandbox import DockerExecutor
from .store import Store, agora
from .teste_bloco import TesteDeBloco
from .validation import nome_passo

log = logging.getLogger("trama.motor")

# Nomes que já foram definidos aqui e hoje moram nos módulos acima; continuam importáveis de ``app.engine``.
__all__ = ["ESTADOS_ROTULO", "MAX_REGISTROS", "NAO_REPETIR", "Cancelado", "Encerrado", "Execucao", "Motor", "Resultado",
           "ResultadoLista", "chave_etapa", "erro_da_sandbox"]


class Motor:
    def __init__(self, store: Store, executor: DockerExecutor, registro: Registro, limites: Limites,
                 workers: int = 4) -> None:
        from concurrent.futures import ThreadPoolExecutor

        self.store = store
        self.historico = Historico(store)
        self.executor = executor
        self.registro = registro
        self.limites = limites
        self.despacho = Despacho(self.historico, executor, limites)
        self.teste = TesteDeBloco(store, self.historico, self.despacho.simples, limites)
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
        return preparar_execucao(flow, project_id, dados_gatilho, registro=self.registro, executor=self.executor,
                                 limites=self.limites, historico=self.historico)

    def iniciar(self, project_id: str, dados_gatilho: dict[str, Any] | None = None, flow: Flow | None = None) -> str:
        """Inicia uma execução do projeto a partir de um gatilho e devolve o id dela (a execução segue em segundo plano).

        É o único caminho para começar uma execução e não depende de HTTP: a API (gatilho manual) passa por aqui, e é
        por aqui que devem passar também o agendador e o webhook. ``flow`` só vem preenchido quando o chamador executa
        uma versão ainda não salva; sem ele vale o fluxo salvo do projeto. Levanta ApiError sem criar nada se o projeto
        não existe, o fluxo está corrompido ou inválido, ou os dados do gatilho não servem.
        """
        projeto = self.store.obter_projeto(project_id)
        if projeto is None:
            raise ApiError(404, "projeto_nao_encontrado", "Projeto não encontrado.")
        try:
            fluxo = flow or Flow.model_validate(projeto["flow"])
        except ValidationError:
            raise ApiError(422, "fluxo_invalido", "O fluxo salvo está corrompido.") from None
        run_id = self.preparar(fluxo, project_id, dados_gatilho)
        self.despachar(run_id)
        return run_id

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
            r = self.despacho.passo(ex, flow.trigger, ())
            falhas = list(r.falhas)
            if r.estado != "falhou":
                falhas = self.despacho.controle.lista(ex, flow.steps, ()).falhas
            else:
                for p in flow.steps:
                    self.despacho.controle.ignorar(ex, p, (), "O gatilho falhou, então o fluxo não seguiu.")
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

    # --------------------------------------------------------------- teste isolado
    def testar_bloco(self, tipo: BlockType, params: dict[str, Any], entradas: dict[str, Any],
                     project_id: str | None = None) -> dict[str, Any]:
        """Executa um único bloco com dados de exemplo e registra a execução (teste do editor de blocos)."""
        return self.teste.testar(tipo, params, entradas, project_id)
