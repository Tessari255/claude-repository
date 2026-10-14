"""Expressões Python inline nos campos (item A1): o formato do campo, o verificador (só análise estática, nada é executado
no processo da API), o modo ``expr`` do executor isolado e o motor, que manda as expressões de um passo em UM trabalho."""

from __future__ import annotations

import builtins
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.config import Limites
from app.models import Campo, Flow
from app.sandbox import DockerExecutor, ExecutorStatus, SandboxResult
from app.validation import BUILTINS_SEGUROS, problema_da_expressao

from .conftest import _montar
from .helpers import (
    analisar_dict,
    campo,
    codigos,
    compor,
    condicao,
    estados,
    etapas,
    expr,
    fluxo,
    lit,
    matematica,
    passo,
    python_inline,
    ref,
    repeticoes,
    saida,
)

RUNNER = Path(__file__).resolve().parents[2] / "executor" / "runner.py"


# ------------------------------------------------------------------ o formato do campo
def test_campo_com_expressao_relê_igual_e_so_emite_a_chave_ativa():
    bruto = expr("len(itens) * 2", itens=("gatilho", "itens"))
    c = Campo.model_validate(bruto)
    assert c.model_dump() == bruto
    assert Campo.model_validate(c.model_dump()) == c
    assert [r.output for r in c.referencias()] == ["itens"]
    assert Campo.model_validate({"expr": "1 + 1"}).model_dump() == {"expr": "1 + 1", "refs": {}}


@pytest.mark.parametrize("bruto", [
    {"expr": "1", "value": 1},
    {"expr": "1", "parts": ["a"]},
    {},
    {"value": 1, "refs": {}},
    {"expr": "x", "refs": {"__builtins__": {"step": "gatilho", "output": "x"}}},
    {"expr": "x", "refs": {"for": {"step": "gatilho", "output": "x"}}},
    {"expr": "x", "refs": {"Nome": {"step": "gatilho", "output": "x"}}},
    {"expr": "x", "refs": {"1a": {"step": "gatilho", "output": "x"}}},
    {"expr": "x", "refs": {"a b": {"step": "gatilho", "output": "x"}}},
    {"expr": "x", "refs": {"if\n": {"step": "gatilho", "output": "x"}}},  # o $ da regex casaria antes do \n final
    {"expr": "x" * 2001},
    {"expr": "x", "refs": {f"v{i}": {"step": "gatilho", "output": "x"} for i in range(21)}},
])
def test_campo_recusa_dois_modos_nenhum_nomes_invalidos_e_excessos(bruto):
    with pytest.raises(ValidationError):
        Campo.model_validate(bruto)


# ------------------------------------------------------------------ o verificador
def nomes_e_numero():
    return [campo("nome", "texto", "ana"), campo("numeros", "lista", [1, 2, 3])]


def test_expressao_correta_nao_tem_problemas_e_o_tipo_fica_para_a_execucao():
    f = fluxo([compor("c", expr("nome.strip().title()", nome=("gatilho", "nome"))),
               matematica("m", expr("sum(numeros) / len(numeros)", numeros=("gatilho", "numeros")), lit(2)),
               saida("s", "R", expr("[n * 2 for n in numeros if n > 1] + list(map(lambda x: x + 1, numeros))",
                                    numeros=("gatilho", "numeros")))], nomes_e_numero())
    a = analisar_dict(f)
    assert a.erros == [], [i.message for i in a.issues]
    assert a.port_types["c"]["outputs"]["resultado"] == "qualquer"  # o Compor herda o tipo do campo: a expressão é 'qualquer'


@pytest.mark.parametrize("codigo, trecho", [
    ("len(nome", "sintaxe"),
    ("nome +", "sintaxe"),
    ("nome.__class__", "__class__"),
    ("open('x')", "“open”"),
    ("__import__('os')", "“__import__”"),
    ("eval('1')", "“eval”"),
    ("outro + 1", "“outro”"),
    ("[y for x in nome]", "“y”"),
    ("(lambda a: b)(1)", "“b”"),
    ("[x for x in x]", "“x”"),  # o primeiro iterável roda fora da compreensão
    ("(lambda a=a: a)()", "“a”"),  # os padrões são avaliados fora da lambda
    ("await nome", "(coluna 1)"),  # o ast aceita; o compilador do runner recusa
    ("(lambda a, a: a)(1, 2)", "(coluna 12)"),
    ("nome(a=1, a=2)", "(coluna 11)"),
    ("(z := 1) + z", "“z”"),
])
def test_expressao_invalida_vira_problema_no_campo(codigo, trecho):
    f = fluxo([compor("c", expr(codigo, nome=("gatilho", "nome")))], nomes_e_numero())
    problema = next(i for i in analisar_dict(f).issues if i.code == "expressao_invalida")
    assert problema.step_id == "c" and problema.field == "entrada" and problema.scope == "configuracao"
    assert trecho in problema.message and problema.hint


def test_erro_de_sintaxe_aponta_a_coluna_em_caracteres():
    msg, dica = problema_da_expressao("'ação' + (", {})  # type: ignore[misc]
    assert "coluna 10" in msg and "parênteses" in dica
    msg, _ = problema_da_expressao("'ç' + nada", {})  # type: ignore[misc]
    assert "“nada”" in msg and "coluna 7" in msg  # o ast conta bytes; a mensagem conta caracteres
    assert "coluna 18" in problema_da_expressao("nome('ção', a=1, a=2)", {})[0]  # type: ignore[index]  # o compilador também conta bytes
    assert "caractere nulo" in problema_da_expressao("a\x00", {})[0]  # type: ignore[index]
    assert "grande demais" in problema_da_expressao("1+" * 1001 + "1", {})[0]  # type: ignore[index]
    # árvores fundas são percorridas sem recursão: o verificador responde em vez de estourar a pilha
    assert problema_da_expressao("-" * 1999 + "1", {}) is None
    assert "“x”" in problema_da_expressao("not " * 490 + "x", {})[0]  # type: ignore[index]


def test_o_fluxo_tem_um_teto_de_caracteres_de_expressao_somados():
    longa = "+".join(["nome"] * 400)  # 1999 caracteres, válida

    def com(n: int) -> list[str]:
        f = fluxo([compor(f"c{i}", expr(longa, nome=("gatilho", "nome"))) for i in range(n)], nomes_e_numero())
        return [i.step_id for i in analisar_dict(f).issues if i.code == "expressao_invalida"]

    assert com(50) == []  # 99.950 caracteres
    assert com(51) == ["c50"]


def test_variaveis_seguem_as_regras_de_visibilidade_do_conteudo_dinamico():
    f = fluxo([compor("a", expr("x * 2", x=("b", "resultado"))), compor("b", lit(1))])
    i = next(i for i in analisar_dict(f).issues if i.code == "referencia_invalida")
    assert i.step_id == "a" and i.field == "entrada" and "só roda depois" in i.message
    sumiu = fluxo([compor("a", expr("x", x=("apagado", "resultado")))])
    assert "referencia_invalida" in codigos(analisar_dict(sumiu))


def test_expressao_em_branco_conta_como_campo_vazio():
    f = fluxo([matematica("m", expr("   "), lit(1))])
    a = analisar_dict(f)
    assert "campo_obrigatorio" in codigos(a) and "expressao_invalida" not in codigos(a)


def test_regras_da_condicao_conferem_as_expressoes_dos_dois_lados():
    f = fluxo([condicao("c", expr("len(nome)", nome=("gatilho", "nome")), "maior", expr("max(numeros) +", numeros=("gatilho", "numeros")))],
              nomes_e_numero())
    i = next(i for i in analisar_dict(f).issues if i.code == "expressao_invalida")
    assert i.field == "regras" and "Comparar com" in i.message
    vazio = fluxo([condicao("c", expr("len(nome)", nome=("gatilho", "nome")), "igual", expr(""))], nomes_e_numero())
    assert "campo_obrigatorio" in codigos(analisar_dict(vazio))
    ok = fluxo([condicao("c", expr("len(nome)", nome=("gatilho", "nome")), "maior", lit(2))], nomes_e_numero())
    assert analisar_dict(ok).erros == []


def test_sem_docker_o_verificador_bloqueia_so_os_passos_com_expressao():
    class Fora:
        disponivel = False
        mensagem = "O Docker não foi encontrado neste computador."
        instrucao = "Instale o Docker."

    f = fluxo([compor("com", expr("1 + 1")), compor("sem", lit(2)),
               condicao("cond", expr("nome", nome=("gatilho", "nome")), "vazio"), compor("branco", expr(" "))],
              nomes_e_numero())
    a = analisar_dict(f, sandbox=Fora())
    assert {i.step_id for i in a.issues if i.code == "executor_indisponivel"} == {"com", "cond"}
    assert "executa código Python" in next(i for i in a.issues if i.code == "executor_indisponivel").message


def test_o_verificador_nunca_executa_a_expressao(monkeypatch):
    def proibido(*_a, **_k):
        raise AssertionError("o verificador executou código")

    monkeypatch.setattr(builtins, "eval", proibido)
    monkeypatch.setattr(builtins, "exec", proibido)
    f = fluxo([compor("c", expr("[n for n in numeros] + [len(nome)]", nome=("gatilho", "nome"), numeros=("gatilho", "numeros")))],
              nomes_e_numero())
    assert analisar_dict(f).erros == []


def test_as_funcoes_permitidas_sao_as_mesmas_no_verificador_e_no_runner():
    spec = importlib.util.spec_from_file_location("runner_da_trama", RUNNER)
    assert spec and spec.loader
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)  # só definições: o runner nada executa ao ser importado
    assert set(runner.EXPR_BUILTINS) == BUILTINS_SEGUROS
    assert {"len", "sum", "min", "max", "sorted", "round", "str", "int", "float"} <= BUILTINS_SEGUROS
    assert not {"eval", "exec", "open", "__import__", "getattr", "compile", "globals", "type", "vars"} & BUILTINS_SEGUROS


def test_entrada_desconhecida_com_expressao_e_so_um_aviso():
    f = fluxo([passo("c", "builtin.compor", {"entrada": lit(1), "outra": expr("1 +")})])
    a = analisar_dict(f)
    assert "entrada_desconhecida" in codigos(a, severity="aviso") and "expressao_invalida" not in codigos(a)


# ------------------------------------------------------------------ o executor isolado (modo expr)
@pytest.mark.docker
def test_expressoes_sao_avaliadas_no_conteiner_com_os_erros_apontando_a_coluna(executor):
    r = executor.run_expr([
        {"id": "a", "code": "nome.strip().title()", "vars": {"nome": "  ana maria "}},
        {"id": "b", "code": "sum(n) / len(n)", "vars": {"n": [1, 2, 3]}},
        {"id": "c", "code": "'ação' + (2 / 0)", "vars": {}},
        {"id": "d", "code": "{1, 2}", "vars": {}},
        {"id": "e", "code": "open('/etc/passwd')", "vars": {}},
        {"id": "f", "code": "(1, None, 'ç')", "vars": {}},
    ])
    assert r.ok, r.error
    itens = {i["id"]: i for i in r.payload["items"]}
    assert [i["id"] for i in r.payload["items"]] == ["a", "b", "c", "d", "e", "f"]  # a ordem do pedido
    assert itens["a"]["value"] == "Ana Maria" and itens["b"]["value"] == 2.0 and itens["f"]["value"] == [1, None, "ç"]
    assert itens["c"]["error"] == {"type": "ZeroDivisionError", "message": "division by zero", "line": 1, "column": 11}
    assert itens["d"]["error"]["type"] == "RetornoInvalido" and "set" in itens["d"]["error"]["message"]
    assert itens["e"]["error"]["type"] == "NameError"  # o runner só oferece as funções permitidas


@pytest.mark.docker
def test_a_barreira_e_o_conteiner_mesmo_se_a_analise_estatica_fosse_contornada(executor):
    # Este caminho por atributos especiais é recusado pelo verificador; mandado direto ao executor, ele alcança `os`,
    # mas dentro do contêiner, como o usuário sem privilégios e sem rede.
    fuga = ("[c for c in ().__class__.__base__.__subclasses__() if c.__name__ == 'catch_warnings'][0]()"
            "._module.__builtins__['__import__']('os')")
    r = executor.run_expr([{"id": "uid", "code": f"{fuga}.getuid()", "vars": {}},
                           {"id": "rede", "code": f"sorted({fuga}.listdir('/sys/class/net'))", "vars": {}}])
    assert r.ok and r.payload["items"] == [{"id": "uid", "value": 65534}, {"id": "rede", "value": ["lo"]}]


@pytest.mark.docker
def test_expressao_sem_fim_e_encerrada_pelo_limite_de_tempo(executor):
    r = executor.run_expr([{"id": "x", "code": "sum(range(10 ** 12))", "vars": {}}])
    assert not r.ok and r.error["category"] == "tempo_esgotado"


def _executor_que_responde(monkeypatch, resposta: SandboxResult) -> DockerExecutor:
    ex = DockerExecutor("falsa", Limites(valor_max=1000))
    monkeypatch.setattr(ex, "status", lambda forcar=False: ExecutorStatus(True, "falsa", lote=True, expr=True))
    monkeypatch.setattr(ex, "run", lambda *a, **k: resposta)
    return ex


PEDIDO = [{"id": "a", "code": "1", "vars": {}}, {"id": "b", "code": "2", "vars": {}}]


@pytest.mark.parametrize("itens", [
    [{"id": "a", "value": 1}],                                       # falta uma resposta
    [{"id": "a", "value": 1}, {"id": "b", "value": 2}, {"id": "c", "value": 3}],  # sobra uma
    [{"id": "b", "value": 2}, {"id": "a", "value": 1}],              # fora de ordem
    [{"id": "a", "value": 1}, {"id": "b", "value": 2, "error": {}}],  # as duas chaves
    [{"id": "a", "value": 1}, {"id": "b"}],                           # nenhuma
    [{"id": "a", "value": 1}, "b"],
])
def test_resposta_forjada_do_runner_e_recusada(monkeypatch, itens):
    ex = _executor_que_responde(monkeypatch, SandboxResult(True, payload={"items": itens}))
    r = ex.run_expr(PEDIDO)
    assert not r.ok and r.error["category"] == "erro_interno"


def test_valores_e_erros_do_runner_so_passam_conferidos(monkeypatch):
    erro_forjado = {"type": "X" * 500, "message": "token=segredo123 " + "m" * 3000, "line": "1", "column": -5, "extra": 1}
    ex = _executor_que_responde(monkeypatch, SandboxResult(True, payload={"items": [
        {"id": "a", "value": "x" * 2000}, {"id": "b", "error": erro_forjado}]}))
    a, b = ex.run_expr(PEDIDO).payload["items"]
    assert a["error"]["type"] == "RetornoInvalido" and "grande demais" in a["error"]["message"]
    assert set(b["error"]) == {"type", "message", "line", "column"}
    assert len(b["error"]["type"]) == 200 and len(b["error"]["message"]) <= 2000 and "segredo123" not in b["error"]["message"]
    assert b["error"]["line"] is None and b["error"]["column"] is None
    nan = _executor_que_responde(monkeypatch, SandboxResult(True, payload={"items": [
        {"id": "a", "value": float("nan")}, {"id": "b", "value": {"k": [1, 2]}}]}))
    a, b = nan.run_expr(PEDIDO).payload["items"]
    assert a["error"]["type"] == "RetornoInvalido" and b == {"id": "b", "value": {"k": [1, 2]}}


def test_imagem_antiga_recusa_expressoes_com_pedido_de_reconstruir(monkeypatch):
    ex = DockerExecutor("antiga", Limites())
    monkeypatch.setattr(ex, "status", lambda forcar=False: ExecutorStatus(True, "antiga", lote=True))
    monkeypatch.setattr(ex, "run", lambda *a, **k: pytest.fail("não devia mandar o trabalho a um runner que não o entende"))
    r = ex.run_expr(PEDIDO)
    assert not r.ok and r.error["category"] == "executor_indisponivel" and "build-executor" in r.error["suggestion"]


# ------------------------------------------------------------------ o motor (executor simulado: nada é avaliado aqui)
@dataclass
class Falha:
    tipo: str
    mensagem: str
    coluna: int


class ExecutorDeExpressoes:
    """Executor falso. O "valor" de cada expressão vem de uma tabela do teste (um valor, uma função das variáveis ou uma
    Falha), então os testes do motor não avaliam código nenhum."""

    def __init__(self, tabela: dict[str, Any]) -> None:
        self.tabela = tabela
        self.trabalhos: list[list[dict]] = []
        self.blocos: list[dict] = []
        self.lotes = 0
        self.ao_calcular = lambda: None
        self.falha_do_trabalho: dict | None = None

    def status(self, forcar: bool = False) -> ExecutorStatus:
        return ExecutorStatus(True, "falsa", lote=True, expr=True)

    def run(self, mode, code, inputs=None, params=None, items=None, limits=None):
        if mode == "check":
            return SandboxResult(True, payload={})
        self.blocos.append(inputs or {})
        return SandboxResult(True, payload={"outputs": {"dobro": (inputs or {})["n"] * 2}})

    def run_lote(self, *a, **k):
        self.lotes += 1
        raise AssertionError("um corpo com expressão não roda em lote")

    def run_expr(self, expressoes, limits=None):
        self.trabalhos.append(expressoes)
        self.ao_calcular()
        if self.falha_do_trabalho:
            return SandboxResult(False, error=self.falha_do_trabalho)
        itens = []
        for e in expressoes:
            r = self.tabela[e["code"]]
            r = r(e["vars"]) if callable(r) else r
            itens.append({"id": e["id"], "error": {"type": r.tipo, "message": r.mensagem, "line": 1, "column": r.coluna}}
                         if isinstance(r, Falha) else {"id": e["id"], "value": r})
        return SandboxResult(True, payload={"items": itens})


def gatilho_padrao():
    return [campo("nome", "texto", "ana"), campo("numeros", "lista", [1, 2, 3])]


def test_todas_as_expressoes_de_um_passo_vao_em_um_unico_trabalho_e_o_historico_guarda_os_valores(settings):
    ex = ExecutorDeExpressoes({"len(numeros)": 3, "sum(numeros)": 6, "len(nome)": 3, "max(numeros)": 3})
    numeros, nome = ("gatilho", "numeros"), ("gatilho", "nome")
    f = fluxo([matematica("m", expr("len(numeros)", numeros=numeros), expr("sum(numeros)", numeros=numeros)),
               condicao("c", expr("len(nome)", nome=nome), "maior", expr("max(numeros)", numeros=numeros),
                        sim=[saida("s1", "Maior", lit("sim"))], nao=[saida("s2", "Não maior", ref("m", "resultado"))])],
              gatilho_padrao())
    run = _montar(settings, ex).executar(f)
    assert run["state"] == "concluido", run["error"]
    assert ex.trabalhos == [
        [{"id": "entrada:a", "code": "len(numeros)", "vars": {"numeros": [1, 2, 3]}},
         {"id": "entrada:b", "code": "sum(numeros)", "vars": {"numeros": [1, 2, 3]}}],
        [{"id": "regra:0:esq", "code": "len(nome)", "vars": {"nome": "ana"}},
         {"id": "regra:0:dir", "code": "max(numeros)", "vars": {"numeros": [1, 2, 3]}}],
    ]
    assert etapas(run)["m"]["inputs"] == {"a": 3, "b": 6} and etapas(run)["m"]["outputs"] == {"resultado": 9}
    assert estados(run)["s1"] == "ignorado" and run["result"]["outputs"] == [{"step_id": "s2", "title": "Não maior", "value": 9}]


def test_erro_da_expressao_aponta_o_campo_e_a_expressao_e_nao_e_repetido(settings):
    ex = ExecutorDeExpressoes({"total / zero": Falha("ZeroDivisionError", "division by zero", 1)})
    f = fluxo([compor("c", expr("total / zero", total=("gatilho", "nome"), zero=("gatilho", "nome")), retry=2)], gatilho_padrao())
    run = _montar(settings, ex).executar(f)
    erro = etapas(run)["c"]["error"]
    assert run["state"] == "falhou" and len(ex.trabalhos) == 1  # sem novas tentativas
    assert erro["code"] == "expressao_invalida"
    assert erro["message"] == "A expressão do campo “Entrada” (`total / zero`) falhou na coluna 1: o código tentou dividir por zero."
    assert erro["technical"] == {"type": "ZeroDivisionError", "message": "division by zero", "snippet": "total / zero"}
    assert "Traceback" not in str(erro)


def test_resultado_de_tipo_errado_e_conferido_contra_o_campo_na_execucao(settings):
    ex = ExecutorDeExpressoes({"nome.upper()": "ANA"})
    run = _montar(settings, ex).executar(fluxo([matematica("m", expr("nome.upper()", nome=("gatilho", "nome")), lit(1))],
                                               gatilho_padrao()))
    erro = etapas(run)["m"]["error"]
    assert erro["code"] == "entrada_invalida"
    assert erro["message"] == "A expressão do campo “A” (`nome.upper()`) devolveu texto, mas o campo espera número."
    assert "float(" in erro["suggestion"]


def test_falha_do_trabalho_inteiro_vira_erro_do_passo(settings):
    ex = ExecutorDeExpressoes({})
    ex.falha_do_trabalho = {"category": "tempo_esgotado", "type": "tempo_esgotado", "message": "O tempo máximo de 4 s foi excedido."}
    run = _montar(settings, ex).executar(fluxo([compor("c", expr("1 + 1"))]))
    erro = etapas(run)["c"]["error"]
    assert erro["code"] == "tempo_esgotado" and erro["message"].startswith("Ao calcular as expressões do passo: o tempo máximo")


def test_repetir_ate_calcula_a_condicao_a_cada_repeticao_e_ignora_o_segundo_valor_de_testes_unarios(settings):
    ex = ExecutorDeExpressoes({"n >= 3": lambda v: v["n"] >= 3})
    regra = {"esq": expr("n >= 3", n=("total", "valor")), "op": "verdadeiro", "dir": expr("nao_deve_rodar")}
    f = fluxo([passo("total", "builtin.var_inicializar", {"inicial": lit(0)}, {"nome": "n", "tipo": "numero"}),
               passo("rep", "builtin.repetir_ate", params={"regras": [regra], "combinador": "e", "limite": 10},
                     slots={"corpo": [passo("inc", "builtin.var_incrementar", params={"variavel": "total"})]}),
               saida("s", "Repetições", ref("rep", "repeticoes"))])
    run = _montar(settings, ex).executar(f)
    assert run["state"] == "concluido", run["error"]
    assert run["result"]["outputs"][0]["value"] == 3
    assert [t[0]["vars"] for t in ex.trabalhos] == [{"n": 1}, {"n": 2}, {"n": 3}]  # um trabalho por teste, com o valor de então
    assert all(len(t) == 1 for t in ex.trabalhos)  # “é verdadeiro” não usa o segundo valor: ele nem vai ao executor


def test_para_cada_com_expressao_no_passo_python_segue_o_caminho_de_um_trabalho_por_iteracao(settings):
    ex = ExecutorDeExpressoes({"item * 10": lambda v: v["item"] * 10})
    corpo = [python_inline("p", "def run(inputs, params):\n    return {'dobro': inputs['n'] * 2}",
                           {"n": ("numero", expr("item * 10", item=("laco", "item")))}, {"dobro": "numero"})]
    f = fluxo([passo("laco", "builtin.para_cada", {"lista": ref("gatilho", "numeros")}, {"limite": 10}, slots={"corpo": corpo})],
              gatilho_padrao())
    run = _montar(settings, ex).executar(f)
    assert run["state"] == "concluido", run["error"]
    assert ex.lotes == 0 and ex.blocos == [{"n": 10}, {"n": 20}, {"n": 30}] and len(ex.trabalhos) == 3
    assert [repeticoes(run, "p")[(i,)]["outputs"] for i in range(3)] == [{"dobro": 20}, {"dobro": 40}, {"dobro": 60}]


def test_cancelar_enquanto_as_expressoes_sao_calculadas_para_antes_do_passo(settings):
    ex = ExecutorDeExpressoes({"1 + 1": 2})
    amb = _montar(settings, ex)
    f = fluxo([python_inline("p", "def run(inputs, params):\n    return {'dobro': 0}", {"n": ("numero", expr("1 + 1"))},
                             {"dobro": "numero"}), compor("depois", expr("1 + 1"))])
    rid = amb.motor.preparar(Flow.model_validate(f), None, None)
    ex.ao_calcular = lambda: amb.motor.cancelar(rid)
    amb.motor.rodar(rid)
    run = amb.store.obter_execucao(rid)
    assert run["state"] == "cancelado" and ex.blocos == [] and len(ex.trabalhos) == 1
    assert estados(run)["p"] == "cancelado"
