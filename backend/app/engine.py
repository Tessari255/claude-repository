"""Motor de execução de fluxos.

* A lógica de execução vive aqui, no backend; o frontend só pede e acompanha.
* A ordem segue as dependências (ordenação topológica), nunca a posição visual.
* Cada execução usa um *snapshot* congelado do fluxo e das definições de bloco (com a
  versão fixada em cada instância), então editar um bloco depois não altera execuções
  nem fluxos existentes.
* Blocos internos rodam aqui (código nosso); código do usuário roda SOMENTE no executor
  isolado.
* O fluxo para na primeira falha de um bloco executado; o diagnóstico é preservado.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .blocks.builtin import HANDLERS, ContextoBloco, requer_sandbox
from .config import Limites
from .errors import ApiError, ErroBloco
from .models import BlockInstance, BlockType, Connection, Flow
from .registry import Registro
from .sandbox import DockerExecutor
from .store import Store, agora
from .tipos import descrever_valor, rotulo_tipo, valor_e_do_tipo, validar_json_puro
from .validation import (analisar, mensagem_parametro, nome_bloco, parametro_visivel, parametros_efetivos,
                         tipo_efetivo_param, valor_efetivo)

log = logging.getLogger("trama.motor")

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
    "excecao": "Use o botão “Testar bloco” com dados de exemplo para investigar e corrigir o código.",
    "sintaxe": "Confira parênteses, dois-pontos (:) e a indentação perto da linha indicada.",
    "tempo_esgotado": "Procure laços sem fim (como `while True`) ou reduza a quantidade de dados processados.",
    "memoria_excedida": "Evite criar listas ou textos enormes; processe menos dados de uma vez.",
    "saida_excessiva": "Reduza o uso de print() e o tamanho do que o código imprime.",
    "retorno_invalido": "Devolva um dicionário com exatamente as saídas declaradas, por exemplo: return {\"mensagem\": texto}.",
    "funcao_ausente": "O código precisa ter `def run(inputs, params):` e devolver um dicionário com as saídas.",
}


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
    elif categoria == "retorno_invalido":
        mensagem = bruto
    elif categoria == "funcao_ausente":
        mensagem = bruto
    elif categoria == "executor_indisponivel":
        mensagem = bruto
    elif categoria == "valor_grande_demais":
        mensagem = bruto
    else:
        mensagem = bruto or "Ocorreu um erro inesperado ao executar o código."
    tecnico = {k: err.get(k) for k in ("type", "message", "line", "snippet", "traceback", "item_index") if err.get(k) not in (None, "")}
    return ErroBloco(mensagem, codigo=_CODIGOS.get(categoria, categoria),
                     sugestao=err.get("suggestion") or _SUGESTOES.get(categoria), tecnico=tecnico or None)


class Motor:
    def __init__(self, store: Store, executor: DockerExecutor, registro: Registro, limites: Limites,
                 workers: int = 4) -> None:
        self.store = store
        self.executor = executor
        self.registro = registro
        self.limites = limites
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="trama-exec")

    def encerrar(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ---------------------------------------------------------------- preparação
    def preparar(self, flow: Flow, project_id: str | None,
                 dados_iniciais: dict[str, Any] | None = None) -> str:
        """Valida TUDO antes de executar e cria o registro da execução (estado: aguardando)."""
        analise = analisar(flow, self.registro.resolver, sandbox=self.executor.status(),
                           ultima_versao=None)
        if analise.erros:
            raise ApiError(422, "fluxo_invalido",
                           "O fluxo tem problemas e não foi executado. Corrija os itens abaixo e tente de novo.",
                           problemas=[i.model_dump() for i in analise.erros])
        dados_iniciais = dados_iniciais or {}
        inicios = {b.id for b in flow.blocks if b.type == "builtin.inicio"}
        for bid, dados in dados_iniciais.items():
            if bid not in inicios or not isinstance(dados, dict):
                raise ApiError(422, "dados_iniciais_invalidos",
                               "Os dados informados para o início do fluxo não são válidos.",
                               sugestao="Informe um objeto JSON para cada bloco “Início manual”.")
        assert analise.order is not None
        definicoes = {f"{b.type}@{b.version}": analise.defs[b.id].model_dump() for b in flow.blocks}
        snapshot = {
            "flow": flow.model_dump(), "definitions": definicoes, "order": analise.order,
            "port_types": analise.port_types, "initial_data": dados_iniciais,
        }
        return self.store.criar_execucao(kind="fluxo", project_id=project_id, snapshot=snapshot, etapas=analise.order)

    def despachar(self, run_id: str) -> None:
        self._pool.submit(self.rodar, run_id)

    # ----------------------------------------------------------------- execução
    def rodar(self, run_id: str) -> None:
        """Executa a execução `run_id` do início ao fim (síncrono)."""
        try:
            self._rodar(run_id)
        except Exception:  # noqa: BLE001 — nunca deixar uma execução presa em "executando"
            log.exception("Falha inesperada no motor (execução %s)", run_id)
            self.store.atualizar_execucao(
                run_id, state="falhou", finished_at=agora(),
                error={"code": "erro_interno", "message": "Ocorreu um erro interno ao executar o fluxo.",
                       "suggestion": "Tente novamente. Se persistir, consulte o log do servidor.", "technical": None})

    def _rodar(self, run_id: str) -> None:
        run = self.store.obter_execucao(run_id, com_snapshot=True)
        assert run is not None
        snap = run["snapshot"]
        flow = Flow.model_validate(snap["flow"])
        defs = {k: BlockType.model_validate(v) for k, v in snap["definitions"].items()}
        blocos = {b.id: b for b in flow.blocks}
        entradas_de: dict[str, list[Connection]] = {}
        for c in flow.connections:
            entradas_de.setdefault(c.target.block, []).append(c)
        tipos_porta = snap["port_types"]

        inicio_run = time.monotonic()
        self.store.atualizar_execucao(run_id, state="executando", started_at=agora())

        estados: dict[str, str] = {}
        motivos: dict[str, str] = {}
        valores: dict[tuple[str, str], Any] = {}
        inativas: set[tuple[str, str]] = set()
        saidas_finais: list[dict[str, Any]] = []
        falha: dict[str, Any] | None = None

        def tipo_de(bid: str) -> BlockType:
            b = blocos[bid]
            return defs[f"{b.type}@{b.version}"]

        for bid in snap["order"]:
            bloco, tipo = blocos[bid], tipo_de(bid)
            nome = nome_bloco(bloco, tipo)

            if falha is not None:
                estados[bid] = "ignorado"
                self.store.atualizar_etapa(
                    run_id, bid, state="ignorado",
                    skip_reason=f"O fluxo foi interrompido porque “{falha['block_name']}” falhou.")
                continue

            # --- caminhos não escolhidos / blocos ignorados a montante
            motivo = None
            for c in entradas_de.get(bid, []):
                if estados.get(c.source.block) == "ignorado":
                    motivo = motivos[c.source.block]
                    break
                if (c.source.block, c.source.port) in inativas:
                    origem = blocos[c.source.block]
                    porta = tipo_de(c.source.block).output(c.source.port)
                    motivo = (f"O caminho “{porta.label if porta else c.source.port}” da condição "
                              f"“{nome_bloco(origem, tipo_de(c.source.block))}” não foi escolhido.")
                    break
            if motivo:
                estados[bid] = "ignorado"
                motivos[bid] = motivo
                self.store.atualizar_etapa(run_id, bid, state="ignorado", skip_reason=motivo)
                continue

            # --- execução do bloco
            entradas = {c.target.port: valores[(c.source.block, c.source.port)] for c in entradas_de.get(bid, [])}
            t0 = time.monotonic()
            self.store.atualizar_etapa(run_id, bid, state="executando", started_at=agora(), inputs=entradas)
            ctx = ContextoBloco(limites=self.limites, dados_iniciais=snap["initial_data"].get(bid))
            try:
                saidas, inativas_bloco = self._executar_bloco(
                    bloco, tipo, entradas, tipos_porta.get(bid, {}).get("outputs", {}), ctx)
            except ErroBloco as e:
                dur = int((time.monotonic() - t0) * 1000)
                estados[bid] = "falhou"
                self.store.atualizar_etapa(run_id, bid, state="falhou", finished_at=agora(), duration_ms=dur,
                                           logs=ctx.logs, error=e.como_dict())
                falha = {"block_id": bid, "block_name": nome, "code": e.codigo, "message": e.mensagem,
                         "suggestion": e.sugestao, "line": (e.tecnico or {}).get("line"), "technical": e.tecnico}
                continue
            except Exception as e:  # noqa: BLE001 — bug em bloco interno: não vazar detalhes
                log.exception("Erro inesperado no bloco %s", bid)
                dur = int((time.monotonic() - t0) * 1000)
                estados[bid] = "falhou"
                erro = ErroBloco("Ocorreu um erro interno ao executar este bloco.", codigo="erro_interno",
                                 tecnico={"type": type(e).__name__})
                self.store.atualizar_etapa(run_id, bid, state="falhou", finished_at=agora(), duration_ms=dur,
                                           logs=ctx.logs, error=erro.como_dict())
                falha = {"block_id": bid, "block_name": nome, "code": erro.codigo, "message": erro.mensagem,
                         "suggestion": None, "line": None, "technical": erro.tecnico}
                continue

            for porta, valor in saidas.items():
                valores[(bid, porta)] = valor
            inativas |= {(bid, p) for p in inativas_bloco}
            if inativas_bloco:
                escolhidas = [o.label for o in tipo.outputs if o.conditional and o.id not in inativas_bloco]
                if escolhidas:
                    ctx.logs.append({"source": "system", "text": f"Teste concluído: seguindo pelo caminho “{escolhidas[0]}”."})
            if tipo.id == "builtin.saida":
                saidas_finais.append({"block_id": bid, "title": bloco.params.get("titulo", "Resultado") or nome,
                                      "value": entradas["valor"]})
            estados[bid] = "concluido"
            self.store.atualizar_etapa(run_id, bid, state="concluido", finished_at=agora(),
                                       duration_ms=int((time.monotonic() - t0) * 1000), outputs=saidas, logs=ctx.logs)

        self.store.atualizar_execucao(
            run_id, state="falhou" if falha else "concluido", finished_at=agora(),
            duration_ms=int((time.monotonic() - inicio_run) * 1000),
            result={"outputs": saidas_finais}, error=falha)

    # ------------------------------------------------------------ um bloco só
    def _executar_bloco(self, bloco: BlockInstance, tipo: BlockType, entradas: dict[str, Any],
                        tipos_saida: dict[str, str], ctx: ContextoBloco) -> tuple[dict[str, Any], set[str]]:
        """Devolve (saídas ativas, portas condicionais inativas). Levanta ErroBloco."""
        for porta in tipo.inputs:
            if porta.id in entradas and not valor_e_do_tipo(entradas[porta.id], porta.type):
                raise ErroBloco(
                    f"A entrada “{porta.label}” esperava {rotulo_tipo(porta.type)}, mas recebeu "
                    f"{descrever_valor(entradas[porta.id])}.",
                    codigo="entrada_invalida",
                    sugestao="Confira o bloco que está ligado a esta entrada.")
            if porta.required and porta.id not in entradas:
                raise ErroBloco(f"A entrada obrigatória “{porta.label}” não recebeu nenhum valor.",
                                codigo="entrada_ausente",
                                sugestao="Ligue esta entrada à saída de outro bloco.")
        params = parametros_efetivos(tipo, bloco.params)

        if requer_sandbox(tipo, params):
            status = self.executor.status()
            if not status.disponivel:
                raise ErroBloco(status.mensagem or "O executor isolado não está disponível.",
                                codigo="executor_indisponivel", sugestao=status.instrucao)
        if tipo.kind == "python":
            r = self.executor.run("block", tipo.code or "", entradas, params)
            ctx.logs.extend(r.logs)
            if not r.ok:
                raise erro_da_sandbox(r.error or {})
            bruto = (r.payload or {}).get("outputs", {})
        else:
            def mapa(codigo: str, itens: list, p: dict) -> list:
                res = self.executor.run("map", codigo, items=itens, params=p)
                ctx.logs.extend(res.logs)
                if not res.ok:
                    raise erro_da_sandbox(res.error or {})
                return (res.payload or {}).get("items", [])

            ctx.mapa_python = mapa
            bruto = HANDLERS[tipo.id](entradas, params, ctx)
        return self._validar_saidas(tipo, bruto, tipos_saida)

    def _validar_saidas(self, tipo: BlockType, bruto: Any, tipos_saida: dict[str, str]) -> tuple[dict[str, Any], set[str]]:
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
        inativas: set[str] = set()
        for pid, porta in declaradas.items():
            if pid not in bruto:
                if porta.conditional:
                    inativas.add(pid)
                    continue
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
                                codigo="retorno_invalido", sugestao=_SUGESTOES["retorno_invalido"])
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
        return saidas, inativas

    # --------------------------------------------------------------- teste isolado
    def testar_bloco(self, tipo: BlockType, params: dict[str, Any], entradas: dict[str, Any],
                     project_id: str | None = None) -> dict[str, Any]:
        """Executa um único bloco com dados de exemplo e registra a execução."""
        bloco = BlockInstance(id="teste", type=tipo.id, version=tipo.version,
                              position={"x": 0, "y": 0}, params=params)  # type: ignore[arg-type]
        problemas: list[dict[str, Any]] = []
        completos = {p.id: valor_efetivo(p, params) for p in tipo.params}
        for p in tipo.params:
            if parametro_visivel(p, tipo, completos):
                msg = mensagem_parametro(p, completos[p.id], tipo_efetivo_param(p, tipo, completos))
                if msg:
                    problemas.append({"code": "parametro_invalido", "severity": "erro", "scope": "configuracao",
                                      "message": msg, "param": p.id})
        for porta in tipo.inputs:
            if porta.required and porta.id not in entradas:
                problemas.append({"code": "entrada_obrigatoria", "severity": "erro", "scope": "configuracao",
                                  "message": f"Informe um valor de exemplo para a entrada “{porta.label}”.", "port": porta.id})
            elif porta.id in entradas and not valor_e_do_tipo(entradas[porta.id], porta.type):
                problemas.append({"code": "tipo_incompativel", "severity": "erro", "scope": "configuracao",
                                  "message": f"O exemplo da entrada “{porta.label}” deveria ser {rotulo_tipo(porta.type)}, "
                                             f"mas é {descrever_valor(entradas[porta.id])}.", "port": porta.id})
        desconhecidas = sorted(set(entradas) - {p.id for p in tipo.inputs})
        for d in desconhecidas:
            problemas.append({"code": "entrada_desconhecida", "severity": "erro", "scope": "configuracao",
                              "message": f"“{d}” não é uma entrada deste bloco."})
        if problemas:
            raise ApiError(422, "teste_invalido", "Corrija os dados de exemplo antes de testar o bloco.", problemas=problemas)

        flow = Flow(blocks=[bloco], connections=[])
        tipos_porta = {"teste": {"inputs": {p.id: p.type for p in tipo.inputs},
                                 "outputs": {p.id: p.type for p in tipo.outputs}}}
        snapshot = {"flow": flow.model_dump(), "definitions": {f"{tipo.id}@{tipo.version}": tipo.model_dump()},
                    "order": ["teste"], "port_types": tipos_porta, "initial_data": {}, "test_inputs": entradas}
        rid = self.store.criar_execucao(kind="bloco", project_id=project_id, snapshot=snapshot, etapas=["teste"])
        self._rodar_teste(rid, bloco, tipo, entradas, tipos_porta["teste"]["outputs"])
        return self.store.obter_execucao(rid)  # type: ignore[return-value]

    def _rodar_teste(self, rid: str, bloco: BlockInstance, tipo: BlockType, entradas: dict[str, Any],
                     tipos_saida: dict[str, str]) -> None:
        t0 = time.monotonic()
        self.store.atualizar_execucao(rid, state="executando", started_at=agora())
        self.store.atualizar_etapa(rid, "teste", state="executando", started_at=agora(), inputs=entradas)
        ctx = ContextoBloco(limites=self.limites)
        try:
            saidas, _ = self._executar_bloco(bloco, tipo, entradas, tipos_saida, ctx)
        except ErroBloco as e:
            dur = int((time.monotonic() - t0) * 1000)
            self.store.atualizar_etapa(rid, "teste", state="falhou", finished_at=agora(), duration_ms=dur,
                                       logs=ctx.logs, error=e.como_dict())
            self.store.atualizar_execucao(rid, state="falhou", finished_at=agora(), duration_ms=dur, result={"outputs": []},
                                          error={"block_id": "teste", "block_name": tipo.name, "code": e.codigo,
                                                 "message": e.mensagem, "suggestion": e.sugestao,
                                                 "line": (e.tecnico or {}).get("line"), "technical": e.tecnico})
            return
        except Exception as e:  # noqa: BLE001
            log.exception("Erro inesperado ao testar bloco %s", tipo.id)
            dur = int((time.monotonic() - t0) * 1000)
            erro = ErroBloco("Ocorreu um erro interno ao executar este bloco.", codigo="erro_interno",
                             tecnico={"type": type(e).__name__})
            self.store.atualizar_etapa(rid, "teste", state="falhou", finished_at=agora(), duration_ms=dur, error=erro.como_dict())
            self.store.atualizar_execucao(rid, state="falhou", finished_at=agora(), duration_ms=dur, error={
                "block_id": "teste", "block_name": tipo.name, **erro.como_dict()})
            return
        dur = int((time.monotonic() - t0) * 1000)
        self.store.atualizar_etapa(rid, "teste", state="concluido", finished_at=agora(), duration_ms=dur,
                                   outputs=saidas, logs=ctx.logs)
        self.store.atualizar_execucao(rid, state="concluido", finished_at=agora(), duration_ms=dur,
                                      result={"outputs": [{"block_id": "teste", "title": tipo.name, "value": saidas}]})
