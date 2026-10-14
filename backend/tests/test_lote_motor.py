"""Laço em lote no motor, com um executor simulado (sem Docker): o que o lote registra no histórico, quando ele é usado e
quando NÃO é, a falha que interrompe, o fallback e o cancelamento.

O contêiner de verdade está em ``test_lote_docker.py``; a prova de que os dois caminhos deixam o mesmo histórico com ele,
em ``test_lote_equivalencia.py``."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from app.models import BlockDraft
from app.sandbox import ExecutorStatus, SandboxResult

from .conftest import _montar
from .helpers import campo, compor, estados, fluxo, historico_normalizado, passo, python_inline, ref, repeticoes, saida


class ExecutorDeLoteFalso:
    """Executor que "roda" o código com uma função do teste: o mesmo resultado por ``run`` (um item) ou ``run_lote`` (todos)."""

    def __init__(self, funcao, lote: bool = True) -> None:
        self.funcao = funcao
        self.lote = lote
        self.chamadas: list[tuple[str, Any]] = []
        self.parou_em: int | None = None
        self.ao_terminar_item = None  # gancho do teste: chamado com a posição antes de entregar o resultado

    def status(self, forcar: bool = False) -> ExecutorStatus:
        return ExecutorStatus(True, "falsa", lote=self.lote)

    def run(self, mode, code, inputs=None, params=None, items=None, limits=None):
        if mode == "check":
            return SandboxResult(True, payload={})
        self.chamadas.append(("run", inputs))
        return self.funcao(inputs or {})

    def run_lote(self, code, itens, params=None, limits=None, *, cancelar=None, ao_item):
        self.chamadas.append(("lote", [dict(i) for i in itens]))
        self.params_do_lote = params
        for posicao, item in enumerate(itens):
            if self.ao_terminar_item:
                self.ao_terminar_item(posicao)
            if cancelar is not None and cancelar.is_set():
                return "cancelado"
            resultado = self.funcao(item)
            continuar = ao_item(posicao, resultado)
            if not resultado.ok:
                return "concluido"
            if continuar is False:
                self.parou_em = posicao
                return "parado"
        return "concluido"


def dobro(entradas: dict) -> SandboxResult:
    return SandboxResult(True, payload={"outputs": {"dobro": entradas["n"] * 2}}, duration_ms=7,
                         logs=[{"source": "stdout", "text": f"item {entradas['n']}\n"}])


def falha_no(item_ruim: int):
    def funcao(entradas: dict) -> SandboxResult:
        if entradas["n"] == item_ruim:
            return SandboxResult(False, logs=[{"source": "stdout", "text": f"item {item_ruim}\n"}], error={
                "category": "excecao", "type": "ZeroDivisionError", "message": "division by zero", "line": 2,
                "snippet": "return 1 / 0", "traceback": "Traceback..."})
        return dobro(entradas)
    return funcao


def passo_python(**kw):
    return python_inline("p", "def run(inputs, params):\n    return {'dobro': inputs['n'] * 2}",
                         {"n": ("numero", ref("laco", "item")), "i": ("numero", ref("laco", "indice"))}, {"dobro": "numero"}, **kw)


def laco(corpo, itens, limite=100):
    return fluxo([passo("laco", "builtin.para_cada", {"lista": ref("gatilho", "l")}, {"limite": limite}, slots={"corpo": corpo}),
                  saida("s", "n", ref("laco", "quantidade"))], [campo("l", "lista", itens)])


def test_laco_de_python_roda_em_um_so_trabalho_e_deixa_uma_linha_por_iteracao(settings):
    ex = ExecutorDeLoteFalso(dobro)
    run = _montar(settings, ex).executar(laco([passo_python()], [10, 20, 30]))
    assert run["state"] == "concluido"
    assert ex.chamadas == [("lote", [{"n": 10, "i": 0}, {"n": 20, "i": 1}, {"n": 30, "i": 2}])]  # um trabalho, nenhum por item
    reps = repeticoes(run, "p")
    assert sorted(reps) == [(0,), (1,), (2,)]
    for i, n in enumerate((10, 20, 30)):
        linha_ = reps[(i,)]
        assert linha_["state"] == "concluido" and linha_["inputs"] == {"n": n, "i": i} and linha_["outputs"] == {"dobro": n * 2}
        assert linha_["logs"] == [{"source": "stdout", "text": f"item {n}\n"}] and linha_["error"] is None
        assert linha_["started_at"] and linha_["finished_at"] and isinstance(linha_["duration_ms"], int)
    assert estados(run)["laco"] == "concluido"
    assert run["result"]["outputs"] == [{"step_id": "s", "title": "n", "value": 3}]


def test_duracao_de_cada_iteracao_e_a_do_proprio_item_e_a_primeira_inclui_a_subida_do_contêiner(settings):
    ex = ExecutorDeLoteFalso(dobro)
    ex.ao_terminar_item = lambda posicao: time.sleep(0.2) if posicao == 0 else None  # o contêiner demora para entregar o primeiro
    run = _montar(settings, ex).executar(laco([passo_python()], [1, 2, 3]))
    reps = repeticoes(run, "p")
    assert reps[(0,)]["duration_ms"] >= 190  # incluiu a espera
    assert reps[(1,)]["duration_ms"] == reps[(2,)]["duration_ms"] == 7  # os demais: o que o runner mediu para o item


def test_iteracao_em_andamento_aparece_como_executando_enquanto_o_contêiner_trabalha(settings):
    amb = _montar(settings, ExecutorDeLoteFalso(dobro))
    vistos: dict[int, dict] = {}
    amb.executor.ao_terminar_item = lambda posicao: vistos.setdefault(posicao, _linhas(amb))  # o que o histórico mostra a essa altura
    amb.executar(laco([passo_python()], [1, 2, 3]))
    assert vistos[0][("p", (0,))] == "executando" and ("p", (1,)) not in vistos[0]  # a primeira já aparece enquanto o contêiner sobe
    assert vistos[2][("p", (0,))] == vistos[2][("p", (1,))] == "concluido" and vistos[2][("p", (2,))] == "executando"


def _linhas(amb) -> dict[tuple[str, tuple[int, ...]], str]:
    with amb.store._conexao() as c:
        rid = c.execute("SELECT id FROM runs").fetchone()["id"]
    return {(s["step_id"], tuple(s["iteration"])): s["state"] for s in amb.store.obter_execucao(rid)["steps"]}


def test_falha_na_iteracao_k_para_o_lote_e_deixa_so_as_linhas_ate_ela(settings):
    ex = ExecutorDeLoteFalso(falha_no(30))
    run = _montar(settings, ex).executar(laco([passo_python(label="Dobrar")], [10, 20, 30, 40, 50]))
    assert run["state"] == "falhou"
    reps = repeticoes(run, "p")
    assert sorted(reps) == [(0,), (1,), (2,)]  # as iterações 3 e 4 nem têm linha
    assert [reps[(i,)]["state"] for i in range(3)] == ["concluido", "concluido", "falhou"]
    erro = reps[(2,)]["error"]
    assert erro["code"] == "excecao_python" and "dividir por zero" in erro["message"] and "linha 2" in erro["message"]
    assert reps[(2,)]["logs"] == [{"source": "stdout", "text": "item 30\n"}]
    laco_ = next(e for e in run["steps"] if e["step_id"] == "laco")
    assert laco_["state"] == "falhou" and laco_["logs"] == [
        {"source": "system", "text": "O item 3 de 5 falhou; as repetições seguintes foram canceladas."}]
    assert run["error"]["step_id"] == "p" and run["error"]["step_name"] == "Dobrar" and run["error"]["line"] == 2
    assert ex.chamadas[0][0] == "lote" and len(ex.chamadas) == 1


def test_saida_invalida_na_iteracao_k_e_constatada_aqui_e_o_lote_e_parado(settings):
    def devolve_texto_no_3(entradas):
        if entradas["n"] == 3:
            return SandboxResult(True, payload={"outputs": {"dobro": "três"}})  # o runner deixou passar; a conferência não
        return dobro(entradas)

    ex = ExecutorDeLoteFalso(devolve_texto_no_3)
    run = _montar(settings, ex).executar(laco([passo_python()], [1, 2, 3, 4, 5]))
    reps = repeticoes(run, "p")
    assert sorted(reps) == [(0,), (1,), (2,)] and reps[(2,)]["state"] == "falhou"
    assert reps[(2,)]["error"]["code"] == "retorno_invalido" and "deveria ser número" in reps[(2,)]["error"]["message"]
    assert ex.parou_em == 2  # o executor foi mandado parar: não sobra contêiner rodando os itens 4 e 5 à toa


def test_bloco_python_da_biblioteca_tambem_vai_em_lote_com_os_parametros_dele(settings):
    ex = ExecutorDeLoteFalso(dobro)
    amb = _montar(settings, ex)
    bloco = BlockDraft.model_validate({
        "name": "Dobrar", "code": "def run(inputs, params):\n    return {'dobro': inputs['n'] * params['fator']}",
        "inputs": [{"id": "n", "label": "N", "type": "numero"}], "outputs": [{"id": "dobro", "label": "Dobro", "type": "numero"}],
        "params": [{"id": "fator", "label": "Fator", "type": "numero", "required": True, "default": 2}]})
    tipo = amb.store.inserir_tipo(bloco.para_tipo("custom.dobrar", 1))
    p = passo("p", tipo.id, {"n": ref("laco", "item")}, {"fator": 3}, versao=tipo.version)
    run = amb.executar(laco([p], [1, 2]))
    assert run["state"] == "concluido" and ex.chamadas == [("lote", [{"n": 1}, {"n": 2}])]
    assert ex.params_do_lote == {"fator": 3}  # os parâmetros do passo seguem para o lote; no passo embutido vão vazios


@pytest.mark.parametrize("corpo,itens,esperado", [
    pytest.param(lambda: [passo_python()], [1, 2, 3], ["lote"], id="padrao"),
    pytest.param(lambda: [passo_python()], [1, 2], ["lote"], id="so_dois_itens"),
    pytest.param(lambda: [passo_python()], [1], ["run"], id="um_item_so"),
    pytest.param(lambda: [passo_python(retry=1)], [1, 2, 3], ["run"] * 3, id="com_tentativas"),
    pytest.param(lambda: [passo_python(timeout=2)], [1, 2, 3], ["run"] * 3, id="com_tempo_proprio"),
    pytest.param(lambda: [passo_python(run_after=["sucesso", "falhou"])], [1, 2, 3], ["run"] * 3, id="executar_apos_diferente"),
    pytest.param(lambda: [compor("c", ref("laco", "item")), passo_python()], [1, 2, 3], ["run"] * 3, id="outro_passo_antes"),
    pytest.param(lambda: [passo_python(), compor("c", ref("p", "dobro"))], [1, 2, 3], ["run"] * 3, id="outro_passo_depois"),
])
def test_so_vai_em_lote_o_corpo_que_e_exatamente_um_passo_python_simples(settings, corpo, itens, esperado):
    ex = ExecutorDeLoteFalso(dobro)
    run = _montar(settings, ex).executar(laco(corpo(), itens))
    assert run["state"] == "concluido", run["error"]
    assert [c[0] for c in ex.chamadas] == esperado


def test_corpo_que_le_a_saida_da_propria_iteracao_anterior_nao_iria_em_lote_mesmo_que_o_verificador_deixasse(settings):
    """O verificador já recusa um passo que lê a própria saída; a conferência daqui é a segunda trava, para o dia em que isso mudar."""
    from app.execucao import Execucao
    from app.lote import LoteDePython
    from app.models import Flow, Passo

    amb = _montar(settings, ExecutorDeLoteFalso(dobro))
    lote = amb.motor.despacho.controle.lote
    assert isinstance(lote, LoteDePython)
    tipo = amb.registro.resolver("builtin.python", 1)
    ex = Execucao(run_id="r", flow=Flow(steps=[]), defs={}, port_types={}, trigger_inputs={}, cancelar=threading.Event())
    laco_ = Passo.model_validate(passo("laco", "builtin.para_cada"))
    lendo_a_propria = Passo.model_validate(passo_python())
    lendo_a_propria.inputs["i"] = Passo.model_validate(compor("x", ref("p", "dobro"))).inputs["entrada"]
    ex.valores["p"] = {"dobro": 1}
    assert lote._entradas(ex, laco_, lendo_a_propria, tipo, [1, 2]) is None
    normal = Passo.model_validate(passo_python())
    ex.valores["laco"] = {}
    assert lote._entradas(ex, laco_, normal, tipo, [1, 2]) == [{"n": 1, "i": 0}, {"n": 2, "i": 1}]


def test_corpo_que_nao_e_python_nunca_chama_o_executor(settings):
    ex = ExecutorDeLoteFalso(dobro)
    run = _montar(settings, ex).executar(laco([compor("c", ref("laco", "item"))], [1, 2, 3]))
    assert run["state"] == "concluido" and ex.chamadas == []


def test_interruptor_interno_forca_um_contêiner_por_iteracao(settings):
    ex = ExecutorDeLoteFalso(dobro)
    amb = _montar(settings, ex)
    amb.motor.despacho.controle.usar_lote = False
    run = amb.executar(laco([passo_python()], [10, 20, 30]))
    assert run["state"] == "concluido"
    assert ex.chamadas == [("run", {"n": 10, "i": 0}), ("run", {"n": 20, "i": 1}), ("run", {"n": 30, "i": 2})]
    amb.motor.despacho.controle.usar_lote = True
    amb.executar(laco([passo_python()], [10, 20, 30]))
    assert ex.chamadas[-1][0] == "lote"


def test_imagem_do_executor_sem_suporte_a_lote_cai_no_caminho_de_sempre(settings):
    ex = ExecutorDeLoteFalso(dobro, lote=False)
    run = _montar(settings, ex).executar(laco([passo_python()], [1, 2]))
    assert run["state"] == "concluido" and [c[0] for c in ex.chamadas] == ["run", "run"]


def test_entrada_que_nao_resolve_em_alguma_iteracao_deixa_o_caminho_de_sempre_falhar_na_iteracao_certa(settings):
    # o terceiro item não tem a chave "n": só ali a entrada deixa de existir, e as duas primeiras iterações rodam antes
    p = python_inline("p", "def run(inputs, params):\n    return {'dobro': 1}", {"n": ("numero", {"parts": [{"step": "laco", "output": "item", "path": "n"}]})},
                      {"dobro": "numero"})
    ex = ExecutorDeLoteFalso(lambda e: SandboxResult(True, payload={"outputs": {"dobro": e["n"]}}))
    run = _montar(settings, ex).executar(laco([p], [{"n": 1}, {"n": 2}, {"x": 3}, {"n": 4}]))
    assert run["state"] == "falhou" and [c[0] for c in ex.chamadas] == ["run", "run"]
    reps = repeticoes(run, "p")
    assert sorted(reps) == [(0,), (1,), (2,)] and reps[(2,)]["error"]["code"] == "campo_ausente"


def test_entradas_que_juntas_passam_do_teto_do_lote_vao_uma_a_uma(settings):
    import dataclasses
    ex = ExecutorDeLoteFalso(dobro)
    pequeno = dataclasses.replace(settings, limites=dataclasses.replace(settings.limites, lote_corpo_max=40))
    run = _montar(pequeno, ex).executar(laco([passo_python()], [1, 2, 3]))  # cada item pesa {"n": 1, "i": 0} = 14 bytes: 3 itens passam de 40
    assert run["state"] == "concluido" and [c[0] for c in ex.chamadas] == ["run"] * 3
    ex2 = ExecutorDeLoteFalso(dobro)
    run = _montar(pequeno, ex2).executar(laco([passo_python()], [1, 2]))  # 2 × 14 = 28 cabem
    assert [c[0] for c in ex2.chamadas] == ["lote"]


def test_laco_dentro_de_laco_roda_um_lote_por_volta_do_laco_de_fora(settings):
    interno = passo("interno", "builtin.para_cada", {"lista": ref("externo", "item")}, {"limite": 5}, slots={"corpo": [
        python_inline("p", "def run(inputs, params):\n    return {'dobro': 0}", {"n": ("numero", ref("interno", "item")),
                                                                                "i": ("numero", ref("interno", "indice"))}, {"dobro": "numero"})]})
    externo = passo("externo", "builtin.para_cada", {"lista": ref("gatilho", "l")}, {"limite": 5}, slots={"corpo": [interno]})
    ex = ExecutorDeLoteFalso(dobro)
    run = _montar(settings, ex).executar(fluxo([externo], [campo("l", "lista", [[1, 2], [3, 4, 5]])]))
    assert run["state"] == "concluido"
    assert ex.chamadas == [("lote", [{"n": 1, "i": 0}, {"n": 2, "i": 1}]), ("lote", [{"n": 3, "i": 0}, {"n": 4, "i": 1}, {"n": 5, "i": 2}])]
    reps = repeticoes(run, "p")
    assert sorted(reps) == [(0, 0), (0, 1), (1, 0), (1, 1), (1, 2)]
    assert reps[(1, 2)]["inputs"] == {"n": 5, "i": 2} and reps[(1, 2)]["outputs"] == {"dobro": 10}


def test_lista_vazia_nao_chama_o_executor_e_ignora_o_corpo(settings):
    ex = ExecutorDeLoteFalso(dobro)
    run = _montar(settings, ex).executar(laco([passo_python()], []))
    assert run["state"] == "concluido" and ex.chamadas == []
    assert estados(run)["p"] == "ignorado"


def test_lista_acima_do_limite_do_laco_falha_antes_de_qualquer_trabalho(settings):
    ex = ExecutorDeLoteFalso(dobro)
    run = _montar(settings, ex).executar(laco([passo_python()], [1, 2, 3, 4], limite=3))
    assert run["state"] == "falhou" and run["error"]["code"] == "limite_itens" and ex.chamadas == []


def test_cancelar_durante_o_lote_para_na_hora_e_a_iteracao_em_andamento_fica_cancelada(settings):
    amb = _montar(settings, ExecutorDeLoteFalso(dobro))
    ex = amb.executor
    chegou_no_item_2 = threading.Event()

    def esperar_no_2(posicao):
        if posicao == 2:
            chegou_no_item_2.set()
            time.sleep(60)  # o contêiner de mentira só acorda quando o cancelamento chega (ver abaixo)

    # o executor de mentira obedece ao evento de cancelamento como o de verdade: sai assim que ele é acionado
    def run_lote(code, itens, params=None, limits=None, *, cancelar=None, ao_item):
        for posicao, item in enumerate(itens):
            if posicao == 2:
                chegou_no_item_2.set()
                if cancelar.wait(30):
                    return "cancelado"
            ao_item(posicao, dobro(item))
        return "concluido"
    ex.run_lote = run_lote  # type: ignore[method-assign]
    ex.ao_terminar_item = esperar_no_2

    from app.models import Flow
    rid = amb.motor.preparar(Flow.model_validate(laco([passo_python()], [1, 2, 3, 4])), None)
    fio = threading.Thread(target=amb.motor.rodar, args=(rid,))
    fio.start()
    assert chegou_no_item_2.wait(10)
    inicio = time.monotonic()
    assert amb.motor.cancelar(rid)
    fio.join(10)
    assert not fio.is_alive() and time.monotonic() - inicio < 5
    run = amb.store.obter_execucao(rid)
    assert run["state"] == "cancelado" and run["result"]["message"] == "A execução foi cancelada."
    reps = repeticoes(run, "p")
    assert sorted(reps) == [(0,), (1,), (2,)]  # nada depois da iteração em andamento
    assert [reps[(i,)]["state"] for i in range(3)] == ["concluido", "concluido", "cancelado"]
    assert estados(run)["laco"] == "cancelado"
    assert not {s["state"] for s in run["steps"]} & {"executando", "aguardando"}


def test_os_dois_caminhos_deixam_o_mesmo_historico_com_o_executor_simulado(settings):
    """O núcleo da prova de equivalência, sem Docker: a mesma sequência de resultados do executor produz o mesmo registro."""
    for funcao, itens in ((dobro, [1, 2, 3, 4]), (falha_no(2), [1, 2, 3, 4]), (falha_no(1), [1, 2, 3, 4])):
        historicos = []
        for usar_lote in (True, False):
            amb = _montar(settings, ExecutorDeLoteFalso(funcao))
            amb.motor.despacho.controle.usar_lote = usar_lote
            run = amb.executar(laco([passo_python()], itens))
            assert amb.executor.chamadas[0][0] == ("lote" if usar_lote else "run")
            historicos.append(historico_normalizado(run))
        assert historicos[0] == historicos[1]
