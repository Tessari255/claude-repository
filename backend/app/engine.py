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

import dataclasses
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .blocks.builtin import (
    HANDLERS, ContextoBloco, avaliar_regra, buscar_caminho, combinar_regras, requer_sandbox, texto_de,
    valor_padrao_do_tipo,
)
from .config import Limites
from .errors import ApiError, ErroBloco
from .models import BlockType, Campo, Flow, Passo, Ref
from .passos import definicao_efetiva, percorrer, regras_declaradas
from .registry import Registro
from .sandbox import DockerExecutor
from .store import Store, agora
from .tipos import descrever_valor, rotulo_tipo, valor_e_do_tipo, validar_json_puro
from .validation import (
    CONTEINERES_DE_LACO, analisar, campo_vazio, mensagem_parametro, nome_passo, parametro_visivel, parametros_efetivos,
    valor_efetivo,
)

log = logging.getLogger("trama.motor")

MAX_REGISTROS = 5000  # linhas de histórico (passos × repetições) por execução

_AMIGAVEIS = {
    "ZeroDivisionError": "O código tentou dividir por zero.",
    "KeyError": "O código procurou uma chave que não existe: {m}.",
    "NameError": "O código usa um nome que não foi definido: {m}.",
    "UnboundLocalError": "O código usa uma variável antes de ela receber um valor: {m}.",
    "TypeError": "O código misturou valores de tipos que não combinam ({m}).",
    "ValueError": "O código recebeu um valor que não consegue usar ({m}).",
    "IndexError": "O código tentou acessar uma posição que não existe ({m}).",
    "AttributeError": "O código tentou usar algo que esse valor não possui ({m}).",
    "ImportError": "O código tentou usar uma biblioteca que não está disponível ({m}).",
    "ModuleNotFoundError": "O código tentou usar uma biblioteca que não está disponível ({m}).",
    "RecursionError": "O código chamou a si mesmo vezes demais (recursão sem fim).",
    "IndentationError": "A indentação (os espaços no começo das linhas) está incorreta.",
    "AssertionError": "Uma verificação (assert) do código falhou.",
}

_CODIGOS = {"excecao": "excecao_python", "sintaxe": "sintaxe_python"}

_SUGESTOES = {
    "excecao": "Revise o código do passo. Os logs e os detalhes técnicos mostram onde ele parou.",
    "sintaxe": "Confira parênteses, dois-pontos (:) e a indentação perto da linha indicada.",
    "tempo_esgotado": "Procure laços sem fim (como `while True`) ou reduza a quantidade de dados processados.",
    "memoria_excedida": "Evite criar listas ou textos enormes; processe menos dados de uma vez.",
    "saida_excessiva": "Reduza o uso de print() e o tamanho do que o código imprime.",
    "retorno_invalido": "Devolva um dicionário com exatamente as saídas declaradas, por exemplo: return {\"mensagem\": texto}.",
    "funcao_ausente": "O código precisa ter `def run(inputs, params):` e devolver um dicionário com as saídas.",
}

# Falhas que repetir não resolve (configuração ou dados errados).
NAO_REPETIR = frozenset({"entrada_invalida", "entrada_ausente", "executor_indisponivel", "conteudo_indisponivel",
                         "parametro_invalido", "limite_itens", "registros_demais", "tipo_invalido"})

ESTADOS_ROTULO = {"concluido": "teve sucesso", "falhou": "falhou", "ignorado": "foi ignorado", "expirou": "expirou"}


def erro_da_sandbox(err: dict[str, Any], quando: str = "") -> ErroBloco:
    """Traduz o erro do executor isolado em uma mensagem para iniciantes + detalhes técnicos."""
    categoria = err.get("category", "erro_interno")
    tipo = err.get("type", "")
    bruto = str(err.get("message", ""))
    linha = err.get("line")
    trecho = err.get("snippet")
    onde = f" (linha {linha}: `{trecho}`)" if linha and trecho else (f" (linha {linha})" if linha else "")
    if categoria == "excecao":
        base = _AMIGAVEIS.get(tipo, f"O código gerou um erro do tipo {tipo or 'desconhecido'}.").replace("{m}", bruto)
        mensagem = base.rstrip(".") + onde + "."
        if err.get("item_index") is not None:
            mensagem = f"No item {err['item_index'] + 1} da lista: " + mensagem[0].lower() + mensagem[1:]
    elif categoria == "sintaxe":
        mensagem = f"Há um erro de escrita (sintaxe) no código{onde}: {bruto}"
    elif categoria == "tempo_esgotado":
        mensagem = bruto.rstrip(".") + (f" (o código estava na linha {linha}: `{trecho}`)." if linha and trecho else ".")
    elif categoria in ("memoria_excedida", "saida_excessiva"):
        mensagem = bruto + (f" (linha {linha})" if linha else "")
    elif categoria in ("retorno_invalido", "funcao_ausente", "executor_indisponivel", "valor_grande_demais"):
        mensagem = bruto
    else:
        mensagem = bruto or "Ocorreu um erro inesperado ao executar o código."
    tecnico = {k: err.get(k) for k in ("type", "message", "line", "snippet", "traceback", "item_index") if err.get(k) not in (None, "")}
    return ErroBloco(mensagem, codigo=_CODIGOS.get(categoria, categoria),
                     sugestao=err.get("suggestion") or _SUGESTOES.get(categoria), tecnico=tecnico or None)


def chave_etapa(step_id: str, iteracao: tuple[int, ...]) -> str:
    return step_id if not iteracao else f"{step_id}@{'.'.join(str(i) for i in iteracao)}"


# --------------------------------------------------------------------------- estado de uma execução
class Cancelado(Exception):
    """O usuário cancelou a execução."""


class Encerrado(Exception):
    """Um passo “Encerrar” terminou a execução."""

    def __init__(self, estado: str, mensagem: str, passo: Passo) -> None:
        super().__init__(mensagem)
        self.estado, self.mensagem, self.passo = estado, mensagem, passo


@dataclass
class Resultado:
    """O que aconteceu com um passo: estado, se foi por tempo esgotado e as falhas que ninguém tratou."""

    estado: str  # concluido | falhou | ignorado
    expirou: bool = False
    falhas: list[dict[str, Any]] = field(default_factory=list)
    mensagem: str = ""  # texto curto do erro (para o “resultado de cada passo” do escopo)


@dataclass
class Execucao:
    run_id: str
    flow: Flow
    defs: dict[str, BlockType]
    port_types: dict[str, dict[str, dict[str, str]]]
    trigger_inputs: dict[str, Any]
    cancelar: threading.Event
    valores: dict[str, dict[str, Any]] = field(default_factory=dict)
    saidas_finais: list[dict[str, Any]] = field(default_factory=list)
    var_tipos: dict[str, str] = field(default_factory=dict)
    nomes: dict[str, str] = field(default_factory=dict)
    posicao: int = 0
    registros: int = 0

    def checar_cancelamento(self) -> None:
        if self.cancelar.is_set():
            raise Cancelado()


def _falha(passo_id: str, nome: str, e: ErroBloco) -> dict[str, Any]:
    return {"step_id": passo_id, "step_name": nome, "code": e.codigo, "message": e.mensagem,
            "suggestion": e.sugestao, "line": (e.tecnico or {}).get("line"), "technical": e.tecnico}


class Motor:
    def __init__(self, store: Store, executor: DockerExecutor, registro: Registro, limites: Limites,
                 workers: int = 4) -> None:
        from concurrent.futures import ThreadPoolExecutor

        self.store = store
        self.executor = executor
        self.registro = registro
        self.limites = limites
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
        return self.store.criar_execucao(kind="fluxo", project_id=project_id, snapshot=snapshot,
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
        except Exception:  # noqa: BLE001 — nunca deixar uma execução presa em "executando"
            log.exception("Falha inesperada no motor (execução %s)", run_id)
            self.store.atualizar_execucao(
                run_id, state="falhou", finished_at=agora(),
                error={"code": "erro_interno", "message": "Ocorreu um erro interno ao executar o fluxo.",
                       "suggestion": "Tente novamente. Se persistir, consulte o log do servidor.", "technical": None})
            self.store.encerrar_etapas_abertas(run_id, "A execução terminou com um erro interno.")
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
                      posicao=self.store.contar_etapas(run_id))
        for pos in percorrer(flow.steps):
            ex.nomes[pos.passo.id] = nome_passo(pos.passo, defs.get(f"{pos.passo.type}@{pos.passo.version}"))
        ex.nomes[flow.trigger.id] = nome_passo(flow.trigger, defs.get(f"{flow.trigger.type}@{flow.trigger.version}"))

        inicio = time.monotonic()
        self.store.atualizar_execucao(run_id, state="executando", started_at=agora())
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
            self.store.encerrar_etapas_abertas(
                run_id, f"A execução foi encerrada pelo passo “{ex.nomes.get(e.passo.id, e.passo.id)}”.", estado_aberto="concluido")
        except Cancelado:
            estado, mensagem = "cancelado", "A execução foi cancelada."
            self.store.encerrar_etapas_abertas(run_id, "A execução foi cancelada.", estado_aberto="cancelado")
        resultado: dict[str, Any] = {"outputs": ex.saidas_finais}
        if mensagem:
            resultado["message"] = mensagem
        self.store.atualizar_execucao(
            run_id, state=estado, finished_at=agora(), duration_ms=int((time.monotonic() - inicio) * 1000),
            result=resultado, error=erro)

    # ----------------------------------------------------------- listas e passos
    def _lista(self, ex: Execucao, passos: list[Passo], iteracao: tuple[int, ...]) -> "ResultadoLista":
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
        self._gravar(ex, passo.id, iteracao, state="ignorado", skip_reason=motivo)
        nome = ex.nomes.get(passo.id, passo.id)
        for filhos in passo.slots.values():
            for f in filhos:
                self._ignorar(ex, f, iteracao, f"O bloco “{nome}” não foi executado.")

    def _gravar(self, ex: Execucao, step_id: str, iteracao: tuple[int, ...], **campos: Any) -> None:
        """Atualiza a linha do passo nesta repetição; cria a linha se ela ainda não existe (passos de laços)."""
        chave = chave_etapa(step_id, iteracao)
        if self.store.garantir_etapa(ex.run_id, chave, step_id, list(iteracao), ex.posicao):
            ex.posicao += 1
            ex.registros += 1
            if ex.registros > MAX_REGISTROS:
                raise ErroBloco(
                    f"A execução passou do limite de {MAX_REGISTROS} registros de passos (passos × repetições).",
                    codigo="registros_demais", sugestao="Reduza o limite de itens do laço ou os passos de dentro dele.")
        self.store.atualizar_etapa(ex.run_id, chave, **campos)

    # ------------------------------------------------------------------ um passo
    def _tipo(self, ex: Execucao, passo: Passo) -> BlockType:
        return ex.defs[f"{passo.type}@{passo.version}"]

    def _passo(self, ex: Execucao, passo: Passo, iteracao: tuple[int, ...]) -> Resultado:
        tipo = self._tipo(ex, passo)
        nome = ex.nomes.get(passo.id, passo.id)
        t0 = time.monotonic()
        ctx = ContextoBloco(limites=self.limites, dados_gatilho=ex.trigger_inputs)
        self._gravar(ex, passo.id, iteracao, state="executando", started_at=agora())
        try:
            if tipo.slots:
                saidas, falhas, extra_logs = self._conteiner(ex, passo, tipo, iteracao, ctx)
                ctx.logs.extend(extra_logs)
                dur = int((time.monotonic() - t0) * 1000)
                if falhas:
                    erro = ErroBloco(
                        f"O passo “{falhas[0]['step_name']}” dentro deste bloco falhou: {falhas[0]['message']}",
                        codigo="falha_em_passo_interno")
                    self._gravar(ex, passo.id, iteracao, state="falhou", finished_at=agora(), duration_ms=dur,
                                 outputs=saidas, logs=ctx.logs, error=erro.como_dict())
                    return Resultado("falhou", False, falhas, falhas[0]["message"])
                self._gravar(ex, passo.id, iteracao, state="concluido", finished_at=agora(), duration_ms=dur,
                             outputs=saidas, logs=ctx.logs)
                return Resultado("concluido")
            saidas = self._folha(ex, passo, tipo, iteracao, ctx)
            self._gravar(ex, passo.id, iteracao, state="concluido", finished_at=agora(),
                         duration_ms=int((time.monotonic() - t0) * 1000), outputs=saidas, logs=ctx.logs)
            return Resultado("concluido")
        except (Cancelado, Encerrado) as e:
            estado = "cancelado" if isinstance(e, Cancelado) or (isinstance(e, Encerrado) and e.estado == "cancelado") else "concluido"
            self._gravar(ex, passo.id, iteracao, state=estado, finished_at=agora(),
                         duration_ms=int((time.monotonic() - t0) * 1000), logs=ctx.logs)
            raise
        except ErroBloco as e:
            dur = int((time.monotonic() - t0) * 1000)
            self._gravar(ex, passo.id, iteracao, state="falhou", finished_at=agora(), duration_ms=dur,
                         logs=ctx.logs, error=e.como_dict())
            return Resultado("falhou", e.codigo == "tempo_esgotado", [_falha(passo.id, nome, e)], e.mensagem)
        except Exception as e:  # noqa: BLE001 — bug em bloco interno: não vazar detalhes
            log.exception("Erro inesperado no passo %s", passo.id)
            erro = ErroBloco("Ocorreu um erro interno ao executar este passo.", codigo="erro_interno",
                             tecnico={"type": type(e).__name__})
            self._gravar(ex, passo.id, iteracao, state="falhou", finished_at=agora(),
                         duration_ms=int((time.monotonic() - t0) * 1000), logs=ctx.logs, error=erro.como_dict())
            return Resultado("falhou", False, [_falha(passo.id, nome, erro)], erro.mensagem)

    # ------------------------------------------------------------ campos dinâmicos
    def _valor_da_ref(self, ex: Execucao, ref: Ref) -> Any:
        saidas = ex.valores.get(ref.step)
        rotulo = f"{ex.nomes.get(ref.step, ref.step)} › {ref.output}"
        if saidas is None or ref.output not in saidas:
            raise ErroBloco(
                f"O conteúdo dinâmico “{rotulo}” não está disponível: o passo não foi executado ou não produziu esse valor.",
                codigo="conteudo_indisponivel",
                sugestao="Confira se o passo de origem roda antes deste e se não foi ignorado ou não falhou.")
        valor = saidas[ref.output]
        if ref.path:
            try:
                return buscar_caminho(valor, ref.path)
            except (KeyError, IndexError, ValueError, TypeError):
                raise ErroBloco(f"O campo “{ref.path}” não existe em “{rotulo}”.", codigo="campo_ausente",
                                sugestao="Confira o nome do campo (use ponto para campos internos, como endereco.cidade).") from None
        return valor

    def _resolver_campo(self, ex: Execucao, campo: Campo) -> Any:
        if not campo.dinamico:
            return campo.value
        partes = campo.parts or []
        if len(partes) == 1 and isinstance(partes[0], Ref):
            return self._valor_da_ref(ex, partes[0])
        pedacos: list[str] = []
        tamanho = 0
        for parte in partes:
            texto = parte if isinstance(parte, str) else texto_de(self._valor_da_ref(ex, parte))
            tamanho += len(texto)
            if tamanho > self.limites.valor_max:
                raise ErroBloco(f"O texto montado ficaria grande demais (mais de {self.limites.valor_max // 1024} KB).",
                                codigo="valor_grande_demais")
            pedacos.append(texto)
        return "".join(pedacos)

    def _entradas(self, ex: Execucao, passo: Passo, ef: BlockType) -> dict[str, Any]:
        entradas: dict[str, Any] = {}
        tipos = ex.port_types.get(passo.id, {}).get("inputs", {})
        for porta in ef.inputs:
            esperado = tipos.get(porta.id, porta.type)
            campo = passo.inputs.get(porta.id)
            if campo is None or campo_vazio(campo, esperado):
                if porta.default is not None:
                    entradas[porta.id] = porta.default
                elif porta.required:
                    raise ErroBloco(f"O campo obrigatório “{porta.label}” não foi preenchido.", codigo="entrada_ausente",
                                    sugestao="Preencha o campo com um valor ou com um conteúdo dinâmico.")
                continue
            valor = self._resolver_campo(ex, campo)
            if not valor_e_do_tipo(valor, esperado):
                raise ErroBloco(
                    f"O campo “{porta.label}” esperava {rotulo_tipo(esperado)}, mas recebeu {descrever_valor(valor)}.",
                    codigo="entrada_invalida", sugestao="Confira o conteúdo dinâmico ou o valor digitado neste campo.")
            entradas[porta.id] = valor
        return entradas

    # ------------------------------------------------------------------ passos simples
    def _folha(self, ex: Execucao, passo: Passo, tipo: BlockType, iteracao: tuple[int, ...], ctx: ContextoBloco) -> dict[str, Any]:
        ef = definicao_efetiva(tipo, passo.params)
        entradas = self._entradas(ex, passo, ef)
        self._gravar(ex, passo.id, iteracao, inputs=entradas)
        params = parametros_efetivos(tipo, passo.params)
        tentativas = 1 + passo.settings.retry.count
        for n in range(1, tentativas + 1):
            ex.checar_cancelamento()
            try:
                saidas = self._executar(ex, passo, tipo, ef, entradas, params, ctx)
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

    def _executar(self, ex: Execucao, passo: Passo, tipo: BlockType, ef: BlockType, entradas: dict[str, Any],
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
                            codigo="retorno_invalido", sugestao=_SUGESTOES["retorno_invalido"])
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
                                codigo="retorno_invalido", sugestao=_SUGESTOES["retorno_invalido"]) from None
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

    # ------------------------------------------------------------------ contêineres
    def _conteiner(self, ex: Execucao, passo: Passo, tipo: BlockType, iteracao: tuple[int, ...],
                   ctx: ContextoBloco) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, str]]]:
        """Executa condição, para cada, repetir até ou escopo. Devolve (saídas, falhas não tratadas, logs)."""
        logs: list[dict[str, str]] = []
        nome = ex.nomes.get(passo.id, passo.id)
        params = parametros_efetivos(tipo, passo.params)
        entradas = self._entradas(ex, passo, tipo)
        self._gravar(ex, passo.id, iteracao, inputs=entradas)

        if tipo.id == "builtin.condicao":
            r = self._avaliar(ex, passo, params)
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
            if falhas or self._avaliar(ex, passo, params):
                break
            if repeticoes >= limite:
                raise ErroBloco(f"A condição não ficou verdadeira em {limite} repetições.", codigo="limite_repeticoes",
                                sugestao="Confira a condição ou aumente o “Limite de repetições” (até 100).")
        saidas = {"repeticoes": repeticoes}
        ex.valores[passo.id] = saidas
        return saidas, falhas, logs

    def _avaliar(self, ex: Execucao, passo: Passo, params: dict[str, Any]) -> bool:
        regras, erro = regras_declaradas(params.get("regras"))
        if erro:
            raise ErroBloco(erro, codigo="parametro_invalido")
        resultados = []
        for r in regras:
            esq = self._resolver_campo(ex, r.esq)
            dir_ = self._resolver_campo(ex, r.dir) if r.dir is not None else None
            resultados.append(avaliar_regra(esq, r.op, dir_))
        return combinar_regras(str(params.get("combinador", "e")), resultados)

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
        rid = self.store.criar_execucao(kind="bloco", project_id=project_id, snapshot=snapshot, etapas=["teste"])
        self._rodar_teste(rid, passo, tipo, entradas, tipos_porta["teste"]["outputs"], params)
        return self.store.obter_execucao(rid)  # type: ignore[return-value]

    def _rodar_teste(self, rid: str, passo: Passo, tipo: BlockType, entradas: dict[str, Any],
                     tipos_saida: dict[str, str], params: dict[str, Any]) -> None:
        t0 = time.monotonic()
        self.store.atualizar_execucao(rid, state="executando", started_at=agora())
        self.store.atualizar_etapa(rid, "teste", state="executando", started_at=agora(), inputs=entradas)
        ctx = ContextoBloco(limites=self.limites)
        ex = Execucao(run_id=rid, flow=Flow(steps=[passo]), defs={}, port_types={"teste": {"inputs": {}, "outputs": tipos_saida}},
                      trigger_inputs={}, cancelar=threading.Event())
        try:
            ef = definicao_efetiva(tipo, params)
            saidas = self._executar(ex, passo, tipo, ef, entradas, parametros_efetivos(tipo, params), ctx)
        except ErroBloco as e:
            dur = int((time.monotonic() - t0) * 1000)
            self.store.atualizar_etapa(rid, "teste", state="falhou", finished_at=agora(), duration_ms=dur,
                                       logs=ctx.logs, error=e.como_dict())
            self.store.atualizar_execucao(rid, state="falhou", finished_at=agora(), duration_ms=dur, result={"outputs": []},
                                          error=_falha("teste", tipo.name, e))
            return
        except Exception as e:  # noqa: BLE001
            log.exception("Erro inesperado ao testar bloco %s", tipo.id)
            dur = int((time.monotonic() - t0) * 1000)
            erro = ErroBloco("Ocorreu um erro interno ao executar este bloco.", codigo="erro_interno",
                             tecnico={"type": type(e).__name__})
            self.store.atualizar_etapa(rid, "teste", state="falhou", finished_at=agora(), duration_ms=dur, error=erro.como_dict())
            self.store.atualizar_execucao(rid, state="falhou", finished_at=agora(), duration_ms=dur,
                                          error=_falha("teste", tipo.name, erro))
            return
        dur = int((time.monotonic() - t0) * 1000)
        self.store.atualizar_etapa(rid, "teste", state="concluido", finished_at=agora(), duration_ms=dur,
                                   outputs=saidas, logs=ctx.logs)
        self.store.atualizar_execucao(rid, state="concluido", finished_at=agora(), duration_ms=dur,
                                      result={"outputs": [{"step_id": "teste", "title": tipo.name, "value": saidas}]})


@dataclass
class ResultadoLista:
    falhas: list[dict[str, Any]]
    resumo: list[dict[str, Any]]
