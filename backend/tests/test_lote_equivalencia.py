"""Prova de equivalência do "Para cada" em lote: cada fluxo roda nos DOIS caminhos (um contêiner para o laço todo, e o de sempre,
um contêiner por iteração, forçado pelo interruptor ``Controle.usar_lote``) e o histórico normalizado (sem ids, horários e
durações) tem de ser idêntico: uma linha ``passo@i`` por iteração, com as mesmas entradas, saídas, logs e erro, e a mesma
semântica de falha. Cada caso também confere QUANTOS trabalhos foram ao executor em cada caminho, para a prova não ser
vazia (um lote que nunca é usado deixaria os dois lados iguais)."""

from __future__ import annotations

import dataclasses
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.models import BlockDraft, Flow

from .conftest import _montar
from .helpers import campo, compor, fluxo, historico_normalizado, lit, passo, python_inline, ref, repeticoes, saida

pytestmark = pytest.mark.docker


@dataclass
class Caso:
    fluxo: dict[str, Any]
    lotes: int                      # trabalhos em lote esperados no caminho novo
    runs: int | None = None         # trabalhos avulsos esperados no caminho de sempre (None: não conferir)
    limites: dict[str, Any] = field(default_factory=dict)
    falha: str | None = None        # código do erro esperado na execução (None: ela termina bem)
    falha_antes: bool = False       # o fluxo é recusado antes de rodar (ApiError)


def corpo(codigo: str, **kw: Any) -> list[dict[str, Any]]:
    return [python_inline("p", codigo, {"n": ("numero", ref("laco", "item")), "i": ("numero", ref("laco", "indice"))}, {"v": "numero"}, **kw)]


def corpo_de_texto(codigo: str) -> list[dict[str, Any]]:
    return [python_inline("p", codigo, {"t": ("texto", ref("laco", "item"))}, {"u": "texto", "n": "numero"})]


def laco(passos: list[dict[str, Any]], itens: list, limite: int = 100, depois: tuple = ()) -> dict[str, Any]:
    return fluxo([passo("laco", "builtin.para_cada", {"lista": ref("gatilho", "l")}, {"limite": limite}, slots={"corpo": passos}),
                  *depois, saida("s", "n", ref("laco", "quantidade"))], [campo("l", "lista", itens)])


OK = "def run(inputs, params):\n    print('item', inputs['n'], 'posição', inputs['i'])\n    return {'v': inputs['n'] * 10 + inputs['i']}\n"


def falha_quando(condicao: str, acao: str) -> str:
    return f"def run(inputs, params):\n    print('antes', inputs['n'])\n    if {condicao}:\n        {acao}\n    return {{'v': inputs['n']}}\n"


# ------------------------------------------------------------------ os casos
def corpo_ok(amb):
    return Caso(laco(corpo(OK), [3, 5, 7, 9]), lotes=1, runs=4)


def falha_na_iteracao_2(amb):
    f = laco(corpo(falha_quando("inputs['n'] == 3", "x = [][5]"), label="Pegar o sexto"), [1, 2, 3, 4, 5])
    return Caso(f, lotes=1, runs=3, falha="excecao_python")


def falha_na_primeira_iteracao(amb):
    return Caso(laco(corpo(falha_quando("True", "x = 1 / 0")), [1, 2, 3]), lotes=1, runs=1, falha="excecao_python")


def falha_na_ultima_iteracao(amb):
    return Caso(laco(corpo(falha_quando("inputs['n'] == 4", "x = 1 / 0")), [1, 2, 3, 4]), lotes=1, runs=4, falha="excecao_python")


def falha_tratada_por_quem_roda_apos(amb):
    trata = compor("trata", lit("tratado"), run_after=["falhou"])
    return Caso(laco(corpo(falha_quando("inputs['n'] == 2", "x = 1 / 0")), [1, 2, 3], depois=(trata,)), lotes=1, runs=2)


def tempo_esgotado_na_iteracao_2(amb):
    f = laco(corpo(falha_quando("inputs['n'] == 3", "while True:\n            pass")), [1, 2, 3, 4, 5])
    return Caso(f, lotes=1, runs=3, limites={"tempo_s": 1.5}, falha="tempo_esgotado")


def tempo_esgotado_tratado_por_executar_apos_falhou(amb):
    trata = compor("trata", lit("tratado"), run_after=["falhou"])
    f = laco(corpo(falha_quando("inputs['n'] == 2", "while True:\n            pass")), [1, 2, 3], depois=(trata,))
    return Caso(f, lotes=1, runs=2, limites={"tempo_s": 1.5})


def tempo_esgotado_dentro_do_laco_nao_e_pego_por_executar_apos_expirou(amb):
    """O laço falha como um todo (não "expira"), então o passo de depois não roda: igual nos dois caminhos."""
    trata = compor("trata", lit("expirou"), run_after=["expirou"])
    f = laco(corpo(falha_quando("inputs['n'] == 2", "while True:\n            pass")), [1, 2, 3], depois=(trata,))
    return Caso(f, lotes=1, runs=2, limites={"tempo_s": 1.5}, falha="tempo_esgotado")


def saida_de_tipo_errado_na_iteracao_2(amb):
    codigo = "def run(inputs, params):\n    return {'v': 'texto'} if inputs['n'] == 3 else {'v': inputs['n']}\n"
    return Caso(laco(corpo(codigo), [1, 2, 3, 4, 5]), lotes=1, runs=3, falha="retorno_invalido")


def saida_que_nao_e_json_na_iteracao_2(amb):
    codigo = "def run(inputs, params):\n    return {'v': {1, 2}} if inputs['n'] == 3 else {'v': inputs['n']}\n"
    return Caso(laco(corpo(codigo), [1, 2, 3, 4]), lotes=1, runs=3, falha="retorno_invalido")


def saida_sem_a_chave_declarada(amb):
    codigo = "def run(inputs, params):\n    return {} if inputs['n'] == 2 else {'v': 1}\n"
    return Caso(laco(corpo(codigo), [1, 2, 3]), lotes=1, runs=2, falha="retorno_invalido")


def saida_com_chave_extra(amb):
    codigo = "def run(inputs, params):\n    return {'v': 1, 'extra': 2} if inputs['n'] == 2 else {'v': 1}\n"
    return Caso(laco(corpo(codigo), [1, 2, 3]), lotes=1, runs=2, falha="retorno_invalido")


def retorno_que_nao_e_dicionario(amb):
    return Caso(laco(corpo("def run(inputs, params):\n    return [1]\n"), [1, 2]), lotes=1, runs=1, falha="retorno_invalido")


def erro_de_sintaxe(amb):
    return Caso(laco(corpo("def run(inputs, params)\n    return {}\n"), [1, 2, 3]), lotes=1, runs=1, falha="sintaxe_python")


def sem_a_funcao_run(amb):
    return Caso(laco(corpo("x = 1\n"), [1, 2]), lotes=1, runs=1, falha="funcao_ausente")


def importando_biblioteca_nao_permitida(amb):
    codigo = "def run(inputs, params):\n    import socket\n    return {'v': 1}\n"
    return Caso(laco(corpo(codigo), [1, 2]), lotes=1, runs=1, falha="excecao_python")


def sai_com_exit(amb):
    return Caso(laco(corpo(falha_quando("inputs['n'] == 2", "exit(3)")), [1, 2, 3]), lotes=1, runs=2, falha="excecao_python")


def processo_morre_no_item_2(amb):
    codigo = ("def run(inputs, params):\n    if inputs['n'] == 3:\n        real = __import__.__closure__[0].cell_contents\n"
              "        real('os')._exit(7)\n    return {'v': inputs['n']}\n")
    return Caso(laco(corpo(codigo), [1, 2, 3, 4]), lotes=1, runs=3, falha="erro_interno")


def memoria_estourada_no_item_2(amb):
    codigo = falha_quando("inputs['n'] == 3", "x = bytearray(400 * 1024 * 1024)")
    return Caso(laco(corpo(codigo), [1, 2, 3, 4]), lotes=1, runs=3, limites={"memoria_mb": 64}, falha="memoria_excedida")


def logs_acima_do_teto_no_item_2(amb):
    codigo = falha_quando("inputs['n'] == 3", "[print('x' * 1000) for _ in range(100)]")
    return Caso(laco(corpo(codigo), [1, 2, 3, 4]), lotes=1, runs=3, limites={"logs_max": 8 * 1024}, falha="saida_excessiva")


def logs_de_todos_os_itens_passam_do_teto_mas_cada_um_cabe(amb):
    codigo = "def run(inputs, params):\n    print('y' * 3000)\n    return {'v': inputs['n']}\n"
    return Caso(laco(corpo(codigo), [1, 2, 3, 4]), lotes=1, runs=4, limites={"logs_max": 4096})


def print_com_unicode_e_sem_quebra_de_linha(amb):
    codigo = "def run(inputs, params):\n    print('ação', inputs['n'], '日本語', end='')\n    print('\\u2028 𝒳 😀')\n    return {'v': inputs['n']}\n"
    return Caso(laco(corpo(codigo), [1, 2, 3]), lotes=1, runs=3)


def lista_vazia(amb):
    return Caso(laco(corpo(OK), []), lotes=0, runs=0)


def um_item_so(amb):
    return Caso(laco(corpo(OK), [4]), lotes=0, runs=1)


def limite_do_laco_excedido(amb):
    return Caso(laco(corpo(OK), [1, 2, 3, 4, 5, 6], limite=5), lotes=0, runs=0, falha="limite_itens")


def itens_com_unicode(amb):
    codigo = "def run(inputs, params):\n    print('lido:', inputs['t'])\n    return {'u': inputs['t'].upper(), 'n': len(inputs['t'])}\n"
    itens = ["ação", "日本語", "𝒳 emoji 😀", "linha separada\u0085e\ttab", "", "aspas \" e \\ barra", "\u0000nulo"]
    return Caso(laco(corpo_de_texto(codigo), itens), lotes=1, runs=len(itens))


def itens_json_variados(amb):
    codigo = "def run(inputs, params):\n    d = inputs['d']\n    return {'d': d, 'chaves': sorted(d)}\n"
    p = python_inline("p", codigo, {"d": ("json", ref("laco", "item"))}, {"d": "json", "chaves": "lista"})
    itens = [{"a": [1, 2.5, None, True, {"x": []}], "b": 9007199254740993, "c": 1e22, "d": 1e-7}, {}, {"unico": "ç"}, {"neg": -0.0, "grande": 10 ** 20}]
    return Caso(laco([p], itens), lotes=1, runs=4)


def valores_grandes_dentro_dos_limites(amb):
    itens = ["ç" * 150_000, "ã" * 150_000]  # ~900 KB de JSON escapado cada um; juntos cabem no teto do lote
    codigo = "def run(inputs, params):\n    return {'u': inputs['t'] + inputs['t'], 'n': len(inputs['t'])}\n"
    return Caso(laco(corpo_de_texto(codigo), itens), lotes=1, runs=2)


def entradas_que_juntas_passam_do_teto_do_lote(amb):
    # cada item cabe num trabalho, os cinco juntos passam do teto do lote: o laço cai no caminho de sempre
    codigo = "def run(inputs, params):\n    return {'u': inputs['t'][:10], 'n': len(inputs['t'])}\n"
    return Caso(laco(corpo_de_texto(codigo), ["x" * 100] * 5), lotes=0, runs=5, limites={"lote_corpo_max": 400})


def resultado_acima_do_limite_de_valor_no_item_2(amb):
    codigo = "def run(inputs, params):\n    return {'u': 'x' * (5000 if inputs['t'] == 'grande' else 3), 'n': 1}\n"
    return Caso(laco(corpo_de_texto(codigo), ["a", "b", "grande", "c"]), lotes=1, runs=3, limites={"valor_max": 2048}, falha="retorno_invalido")


def entrada_grande_demais_para_um_trabalho_no_item_2(amb):
    # o item 2 aparece em 6 entradas: 6 × 6 KB passa de 4 × valor_max (o limite do que se manda de uma vez ao contêiner)
    codigo = "def run(inputs, params):\n    return {'u': 'ok', 'n': len(inputs['a'])}\n"
    entradas = {k: ("texto", ref("laco", "item")) for k in "abcdef"}
    p = python_inline("p", codigo, entradas, {"u": "texto", "n": "numero"})
    return Caso(laco([p], ["x", "y" * 500, "z" * 6000, "w"]), lotes=1, runs=3, limites={"valor_max": 8192}, falha="valor_grande_demais")


def estado_global_do_modulo_nao_passa_de_um_item_ao_outro(amb):
    codigo = ("contador = 0\nvistos = []\n\ndef run(inputs, params):\n    global contador\n    contador += 1\n    vistos.append(inputs['n'])\n"
              "    try:\n        antes = open('/tmp/anterior').read()\n    except OSError:\n        antes = ''\n    open('/tmp/anterior', 'w').write('x')\n"
              "    params_mudou = bool(params)\n    return {'v': contador * 1000 + len(vistos) * 100 + len(antes) * 10 + int(params_mudou)}\n")
    return Caso(laco(corpo(codigo), [1, 2, 3, 4]), lotes=1, runs=4)


def bloco_python_da_biblioteca(amb):
    draft = BlockDraft.model_validate({
        "name": "Somar fator", "code": "def run(inputs, params):\n    print('fator', params['fator'])\n    return {'v': inputs['n'] * params['fator']}\n",
        "inputs": [{"id": "n", "label": "N", "type": "numero"}], "outputs": [{"id": "v", "label": "V", "type": "numero"}],
        "params": [{"id": "fator", "label": "Fator", "type": "numero", "required": True, "default": 2}]})
    tipo = amb.store.inserir_tipo(draft.para_tipo("custom.somar_fator", 1))
    p = passo("p", tipo.id, {"n": ref("laco", "item")}, {"fator": 3}, versao=tipo.version)
    return Caso(laco([p], [1, 2, 3]), lotes=1, runs=3)


def bloco_python_da_biblioteca_que_falha_no_item_2(amb):
    draft = BlockDraft.model_validate({
        "name": "Divide", "code": "def run(inputs, params):\n    return {'v': 10 // inputs['n']}\n",
        "inputs": [{"id": "n", "label": "N", "type": "numero"}], "outputs": [{"id": "v", "label": "V", "type": "numero"}], "params": []})
    tipo = amb.store.inserir_tipo(draft.para_tipo("custom.divide", 1))
    p = passo("p", tipo.id, {"n": ref("laco", "item")}, versao=tipo.version)
    return Caso(laco([p], [5, 2, 0, 1]), lotes=1, runs=3, falha="excecao_python")


def laco_dentro_de_laco(amb):
    interno = passo("interno", "builtin.para_cada", {"lista": ref("externo", "item")}, {"limite": 5}, slots={"corpo": [
        python_inline("p", OK, {"n": ("numero", ref("interno", "item")), "i": ("numero", ref("interno", "indice"))}, {"v": "numero"})]})
    externo = passo("externo", "builtin.para_cada", {"lista": ref("gatilho", "l")}, {"limite": 5}, slots={"corpo": [interno]})
    return Caso(fluxo([externo], [campo("l", "lista", [[1, 2], [3, 4, 5]])]), lotes=2, runs=5)


def laco_dentro_de_condicao_e_escopo(amb):
    dentro = passo("laco", "builtin.para_cada", {"lista": ref("gatilho", "l")}, {"limite": 5}, slots={"corpo": corpo(OK)})
    escopo = passo("escopo", "builtin.escopo", slots={"corpo": [dentro]})
    return Caso(fluxo([escopo, saida("s", "n", ref("laco", "quantidade"))], [campo("l", "lista", [1, 2, 3])]), lotes=1, runs=3)


# Corpos que não se encaixam: caem no caminho de sempre e o resultado continua o mesmo.
def corpo_com_dois_passos(amb):
    outro = compor("c", ref("laco", "item"))
    return Caso(laco([outro, *corpo(OK)], [1, 2, 3]), lotes=0, runs=3)


def passo_com_tentativas(amb):
    return Caso(laco(corpo(falha_quando("inputs['n'] == 2", "x = 1 / 0"), retry=1), [1, 2, 3]), lotes=0, falha="excecao_python")


def passo_com_tempo_proprio(amb):
    return Caso(laco(corpo(OK, timeout=3), [1, 2, 3]), lotes=0, runs=3)


def passo_com_executar_apos_diferente(amb):
    return Caso(laco(corpo(OK, run_after=["sucesso", "falhou"]), [1, 2, 3]), lotes=0, runs=3)


def entrada_que_nao_resolve_na_terceira_iteracao(amb):
    p = python_inline("p", "def run(inputs, params):\n    return {'v': inputs['n']}\n",
                      {"n": ("numero", {"parts": [{"step": "laco", "output": "item", "path": "n"}]})}, {"v": "numero"})
    return Caso(laco([p], [{"n": 1}, {"n": 2}, {"x": 3}, {"n": 4}]), lotes=0, runs=2, falha="campo_ausente")


CASOS: dict[str, Callable[[Any], Caso]] = {f.__name__: f for f in (
    corpo_ok, falha_na_iteracao_2, falha_na_primeira_iteracao, falha_na_ultima_iteracao, falha_tratada_por_quem_roda_apos,
    tempo_esgotado_na_iteracao_2, tempo_esgotado_tratado_por_executar_apos_falhou,
    tempo_esgotado_dentro_do_laco_nao_e_pego_por_executar_apos_expirou,
    saida_de_tipo_errado_na_iteracao_2, saida_que_nao_e_json_na_iteracao_2, saida_sem_a_chave_declarada, saida_com_chave_extra,
    retorno_que_nao_e_dicionario, erro_de_sintaxe, sem_a_funcao_run, importando_biblioteca_nao_permitida, sai_com_exit,
    processo_morre_no_item_2, memoria_estourada_no_item_2, logs_acima_do_teto_no_item_2,
    logs_de_todos_os_itens_passam_do_teto_mas_cada_um_cabe, print_com_unicode_e_sem_quebra_de_linha,
    lista_vazia, um_item_so, limite_do_laco_excedido, itens_com_unicode, itens_json_variados, valores_grandes_dentro_dos_limites,
    entradas_que_juntas_passam_do_teto_do_lote, resultado_acima_do_limite_de_valor_no_item_2,
    entrada_grande_demais_para_um_trabalho_no_item_2, estado_global_do_modulo_nao_passa_de_um_item_ao_outro,
    bloco_python_da_biblioteca, bloco_python_da_biblioteca_que_falha_no_item_2, laco_dentro_de_laco, laco_dentro_de_condicao_e_escopo,
    corpo_com_dois_passos, passo_com_tentativas, passo_com_tempo_proprio, passo_com_executar_apos_diferente,
    entrada_que_nao_resolve_na_terceira_iteracao,
)}


# ------------------------------------------------------------------ o harness
@pytest.fixture()
def uso(monkeypatch, executor):
    """Conta os trabalhos que chegam ao executor, por tipo, sem tirar nada do caminho."""
    contagem = {"run": 0, "lote": 0}
    run_original, lote_original = executor.run, executor.run_lote

    def run(mode, *args, **kwargs):
        if mode != "check":
            contagem["run"] += 1
        return run_original(mode, *args, **kwargs)

    def run_lote(*args, **kwargs):
        contagem["lote"] += 1
        return lote_original(*args, **kwargs)

    monkeypatch.setattr(executor, "run", run)
    monkeypatch.setattr(executor, "run_lote", run_lote)
    return contagem


def ambiente(settings, executor, limites: dict[str, Any]):
    return _montar(dataclasses.replace(settings, limites=dataclasses.replace(settings.limites, **limites)), executor)


@pytest.mark.parametrize("nome", list(CASOS))
def test_os_dois_caminhos_deixam_o_mesmo_historico(nome, settings, executor, uso):
    amb0 = ambiente(settings, executor, {})
    caso = CASOS[nome](amb0)
    amb = ambiente(settings, executor, caso.limites)
    historicos = {}
    trabalhos = {}
    for caminho in ("lote", "sequencial"):
        amb.motor.despacho.controle.usar_lote = caminho == "lote"
        uso.update(run=0, lote=0)
        run = amb.executar(caso.fluxo)
        historicos[caminho], trabalhos[caminho] = historico_normalizado(run), dict(uso)
        if caso.falha:
            assert run["state"] == "falhou" and run["error"]["code"] == caso.falha, (caminho, run["state"], run["error"])
        else:
            assert run["state"] == "concluido", (caminho, run["error"])  # inclusive quando um passo posterior trata a falha
    assert historicos["lote"] == historicos["sequencial"]
    assert trabalhos["lote"]["lote"] == caso.lotes, trabalhos  # o caminho novo foi (ou não foi) usado como esperado
    assert trabalhos["sequencial"]["lote"] == 0  # o interruptor força o de sempre
    if caso.lotes:
        assert trabalhos["lote"]["run"] == 0  # e quando o lote roda, nenhuma iteração vai avulsa
    if caso.runs is not None:
        assert trabalhos["sequencial"]["run"] == caso.runs, trabalhos
        if not caso.lotes:
            assert trabalhos["lote"]["run"] == caso.runs


def test_a_prova_nao_e_vazia_cada_linha_de_iteracao_traz_entradas_saidas_logs_e_erro(settings, executor, uso):
    amb = ambiente(settings, executor, {})
    run = amb.executar(CASOS["falha_na_iteracao_2"](amb).fluxo)
    reps = repeticoes(run, "p")
    assert sorted(reps) == [(0,), (1,), (2,)]  # as iterações 3 e 4 nem ganham linha
    assert reps[(0,)]["inputs"] == {"n": 1, "i": 0} and reps[(0,)]["outputs"] == {"v": 1}
    assert reps[(1,)]["logs"] == [{"source": "stdout", "text": "antes 2\n"}]
    erro = reps[(2,)]["error"]
    assert reps[(2,)]["state"] == "falhou" and erro["code"] == "excecao_python" and erro["technical"]["line"] == 4
    assert "(list index out of range) (linha 4: `x = [][5]`)." in erro["message"]
    assert run["error"]["step_name"] == "Pegar o sexto" and run["error"]["line"] == 4
    assert uso["lote"] == 1 and uso["run"] == 0


# ------------------------------------------------------------------ cancelamento
def esperar_linha(amb, run_id: str, chave: tuple[str, tuple[int, ...]], estado: str, limite_s: float = 30) -> None:
    fim = time.monotonic() + limite_s
    while time.monotonic() < fim:
        linhas = {(s["step_id"], tuple(s["iteration"])): s["state"] for s in amb.store.obter_execucao(run_id)["steps"]}
        if linhas.get(chave) == estado:
            return
        time.sleep(0.02)
    raise AssertionError(f"{chave} não chegou a {estado}")


def cancelar_no_item_1(amb, dormir_s: float, usar_lote: bool):
    codigo = f"import time\ndef run(inputs, params):\n    if inputs['n'] == 2:\n        time.sleep({dormir_s})\n    return {{'v': inputs['n']}}\n"
    f = laco(corpo(codigo), [1, 2, 3, 4])
    amb.motor.despacho.controle.usar_lote = usar_lote
    rid = amb.motor.preparar(Flow.model_validate(f), None)
    fio = threading.Thread(target=amb.motor.rodar, args=(rid,))
    fio.start()
    esperar_linha(amb, rid, ("p", (1,)), "executando")
    time.sleep(0.5)  # o item 1 já está dormindo
    inicio = time.monotonic()
    assert amb.motor.cancelar(rid)
    fio.join(60)
    assert not fio.is_alive()
    return amb.store.obter_execucao(rid), time.monotonic() - inicio


def test_cancelar_deixa_a_execucao_cancelada_nos_dois_caminhos_so_a_iteracao_em_andamento_difere(settings, executor):
    amb = ambiente(settings, executor, {})
    do_lote, _ = cancelar_no_item_1(amb, 2, True)
    sequencial, _ = cancelar_no_item_1(amb, 2, False)
    for run in (do_lote, sequencial):
        assert run["state"] == "cancelado" and run["result"]["message"] == "A execução foi cancelada."
        reps = repeticoes(run, "p")
        assert sorted(reps) == [(0,), (1,)]  # nada começa depois do cancelamento
        assert {s["step_id"]: s["state"] for s in run["steps"] if not s["iteration"]}["laco"] == "cancelado"
        assert not {s["state"] for s in run["steps"]} & {"executando", "aguardando"}
    a, b = repeticoes(do_lote, "p"), repeticoes(sequencial, "p")
    assert historico_normalizado({**do_lote, "steps": [a[(0,)]]})["steps"] == historico_normalizado({**sequencial, "steps": [b[(0,)]]})["steps"]
    assert a[(0,)]["state"] == "concluido"
    assert a[(1,)]["state"] == "cancelado"       # o contêiner foi abatido com a iteração dentro
    assert b[(1,)]["state"] == "concluido"       # o de sempre espera o contêiner da iteração terminar


def test_cancelar_um_lote_abate_o_contêiner_sem_esperar_o_item_em_andamento(settings, executor):
    amb = ambiente(settings, executor, {"tempo_s": 120.0})
    run, depois_de_cancelar_s = cancelar_no_item_1(amb, 60, True)  # o item dormiria 60 s
    assert run["state"] == "cancelado" and depois_de_cancelar_s < 5
    assert [s["state"] for _, s in sorted(repeticoes(run, "p").items())] == ["concluido", "cancelado"]
    sobras = subprocess.run(["docker", "ps", "-aq", "--filter", "label=trama.executor=1"], capture_output=True, text=True).stdout.split()
    assert sobras == []
