"""Motor de execução com blocos internos (não precisa de Docker): sequência, “executar após”, condição, laços,
escopos, encerrar, variáveis, conteúdo dinâmico, tentativas, cancelamento e diagnóstico por passo."""

from __future__ import annotations

import threading
import time

import pytest

from app import engine
from app.errors import ApiError

from .helpers import campo, compor, condicao, estados, etapas, fluxo, lit, matematica, passo, ref, repeticoes, saida, tpl


def soma(a=2, b=3, operacao="somar"):
    return fluxo([matematica("soma", ref("gatilho", "a"), ref("gatilho", "b"), operacao), saida("saida", "Soma", ref("soma", "resultado"))],
                 [campo("a", "numero", a), campo("b", "numero", b)])


def valores(run):
    return {o["title"]: o["value"] for o in run["result"]["outputs"]}


# ------------------------------------------------------------------ sequência básica
def test_dois_numeros_do_gatilho_e_uma_soma(sem_docker):
    run = sem_docker.executar(soma(2, 3))
    assert run["state"] == "concluido"
    assert run["result"]["outputs"] == [{"step_id": "saida", "title": "Soma", "value": 5}]
    assert set(estados(run).values()) == {"concluido"}


@pytest.mark.parametrize("op,a,b,esperado", [
    ("subtrair", 10, 4, 6), ("multiplicar", 6, 7, 42), ("dividir", 9, 3, 3), ("dividir", 7, 2, 3.5),
    ("resto", 10, 4, 2), ("potencia", 2, 10, 1024), ("minimo", 3, 8, 3), ("maximo", 3, 8, 8),
])
def test_operacoes_matematicas(sem_docker, op, a, b, esperado):
    assert valores(sem_docker.executar(soma(a, b, op)))["Soma"] == esperado


def test_dados_do_gatilho_substituem_os_padroes_e_sao_conferidos(sem_docker):
    assert valores(sem_docker.executar(soma(2, 3), {"a": 10}))["Soma"] == 13
    for dados, trecho in (({"a": "x"}, "deveria ser número"), ({"zzz": 1}, "não tem o campo"), ):
        with pytest.raises(ApiError) as exc:
            sem_docker.executar(soma(), dados)
        assert exc.value.codigo == "dados_invalidos" and trecho in exc.value.problemas[0]["message"]


def test_campo_obrigatorio_do_gatilho_sem_padrao_precisa_ser_informado(sem_docker):
    f = fluxo([saida("s", "Nome", ref("gatilho", "nome"))], [campo("nome", "texto", required=True)])
    with pytest.raises(ApiError) as exc:
        sem_docker.executar(f)
    assert "Preencha o campo" in exc.value.problemas[0]["message"]
    assert valores(sem_docker.executar(f, {"nome": "Ana"})) == {"Nome": "Ana"}


def test_campo_opcional_do_gatilho_sem_valor_vira_o_valor_neutro_do_tipo(sem_docker):
    f = fluxo([saida("s1", "t", ref("gatilho", "t")), saida("s2", "n", ref("gatilho", "n")), saida("s3", "l", ref("gatilho", "l"))],
              [campo("t"), campo("n", "numero"), campo("l", "lista")])
    assert valores(sem_docker.executar(f)) == {"t": "", "n": 0, "l": []}


def test_divisao_por_zero_falha_com_mensagem_clara_e_os_seguintes_ficam_ignorados(sem_docker):
    f = soma(1, 0, "dividir")
    f["steps"].insert(1, compor("depois", ref("soma", "resultado")))
    run = sem_docker.executar(f)
    assert run["state"] == "falhou"
    assert run["error"]["step_id"] == "soma" and "dividir por zero" in run["error"]["message"]
    e = etapas(run)
    assert e["soma"]["state"] == "falhou" and e["soma"]["error"]["code"] == "divisao_por_zero"
    assert e["soma"]["inputs"] == {"a": 1, "b": 0}  # diagnóstico preservado
    assert e["depois"]["state"] == "ignorado" and "falhou" in e["depois"]["skip_reason"] and "Operação matemática" in e["depois"]["skip_reason"]
    assert e["saida"]["state"] == "ignorado"
    assert run["result"]["outputs"] == []


def test_os_passos_rodam_na_ordem_da_lista_e_o_historico_registra_essa_ordem(sem_docker):
    run = sem_docker.executar(soma())
    ordem = [e["step_id"] for e in sorted(run["steps"], key=lambda e: e["position"])]
    assert ordem == ["gatilho", "soma", "saida"]


def test_um_conteudo_pode_alimentar_varios_passos(sem_docker):
    f = fluxo([matematica("dobro", ref("gatilho", "n"), lit(2), "multiplicar"), matematica("mais1", ref("gatilho", "n"), lit(1)),
               saida("s1", "Dobro", ref("dobro", "resultado")), saida("s2", "Mais um", ref("mais1", "resultado"))], [campo("n", "numero", 5)])
    assert valores(sem_docker.executar(f)) == {"Dobro": 10, "Mais um": 6}


# ------------------------------------------------------------------ conteúdo dinâmico
def test_texto_com_conteudo_dinamico_e_uma_unica_referencia_preserva_o_tipo(sem_docker):
    f = fluxo([compor("a", tpl("Olá, ", ("gatilho", "nome"), "! Você tem ", ("gatilho", "idade"), " anos.")),
               compor("b", ref("gatilho", "idade")), saida("s1", "texto", ref("a", "resultado")), saida("s2", "tipo", ref("b", "resultado"))],
              [campo("nome", "texto", "Ana"), campo("idade", "numero", 30)])
    assert valores(sem_docker.executar(f)) == {"texto": "Olá, Ana! Você tem 30 anos.", "tipo": 30}


def test_caminho_dentro_de_um_objeto_ou_lista(sem_docker):
    dados = {"usuario": {"nome": "Ana", "tags": ["a", "b"]}, "n": 2.5}
    f = fluxo([saida("s1", "nome", ref("gatilho", "d", "usuario.nome")), saida("s2", "tag", ref("gatilho", "d", "usuario.tags.1")),
               compor("c", tpl("n=", ("gatilho", "d"))), saida("s3", "todo", ref("c", "resultado"))], [campo("d", "json", dados)])
    v = valores(sem_docker.executar(f))
    assert v["nome"] == "Ana" and v["tag"] == "b" and v["todo"].startswith("n={")  # objeto dentro de texto vira JSON


def test_caminho_inexistente_explica_qual_campo_faltou(sem_docker):
    f = fluxo([compor("c", ref("gatilho", "d", "usuario.email"))], [campo("d", "json", {"usuario": {}})])
    run = sem_docker.executar(f)
    assert run["state"] == "falhou" and "“usuario.email” não existe" in run["error"]["message"] and run["error"]["code"] == "campo_ausente"


def test_conteudo_de_um_passo_ignorado_nao_esta_disponivel(sem_docker):
    # o passo "b" só roda se "a" falhar; "c" tenta usar a saída de "b", que foi ignorado
    f = fluxo([passo("a", "builtin.compor", {"entrada": lit(1)}), compor("b", lit(2), run_after=["falhou"]),
               compor("c", ref("b", "resultado"), run_after=["sucesso", "ignorado"])])
    run = sem_docker.executar(f)
    e = etapas(run)
    assert e["b"]["state"] == "ignorado" and e["c"]["state"] == "falhou"
    assert e["c"]["error"]["code"] == "conteudo_indisponivel" and "não está disponível" in e["c"]["error"]["message"]


def test_texto_montado_grande_demais_e_recusado(sem_docker):
    f = fluxo([compor("c", tpl(*[("gatilho", "t")] * 20))], [campo("t", "texto", "x" * 100_000)])
    run = sem_docker.executar(f)
    assert run["error"]["code"] == "valor_grande_demais"


# ------------------------------------------------------------------ executar após
def test_passo_com_executar_apos_falhou_captura_o_erro_e_a_execucao_termina_com_sucesso(sem_docker):
    f = fluxo([matematica("quebra", lit(1), lit(0), "dividir"),
               compor("captura", lit("capturado"), run_after=["falhou"]), saida("s", "Resultado", ref("captura", "resultado"))])
    run = sem_docker.executar(f)
    assert estados(run) == {"gatilho": "concluido", "quebra": "falhou", "captura": "concluido", "s": "concluido"}
    assert run["state"] == "concluido" and run["error"] is None  # falha tratada: a execução termina com sucesso
    assert valores(run) == {"Resultado": "capturado"}


def test_executar_apos_olha_so_o_passo_imediatamente_anterior_por_isso_se_usa_escopo(sem_docker):
    # como no Power Automate: se um passo no meio é ignorado, quem vem depois dele enxerga "ignorado", não a falha de antes
    f = fluxo([matematica("quebra", lit(1), lit(0), "dividir"), compor("pulado", lit("não roda")),
               compor("captura", lit("capturado"), run_after=["falhou"])])
    run = sem_docker.executar(f)
    assert estados(run)["pulado"] == "ignorado" and estados(run)["captura"] == "ignorado" and run["state"] == "falhou"


def test_falha_que_ninguem_trata_deixa_a_execucao_como_falhou(sem_docker):
    f = fluxo([matematica("quebra", lit(1), lit(0), "dividir"), compor("depois", lit(1))])
    assert sem_docker.executar(f)["state"] == "falhou"


def test_executar_apos_ignorado_e_executar_apos_sucesso_ou_falha(sem_docker):
    f = fluxo([matematica("quebra", lit(1), lit(0), "dividir"),
               compor("pulado", lit(1)),                                         # sucesso → ignorado (anterior falhou)
               compor("limpeza", lit("limpo"), run_after=["ignorado"]),          # roda porque o anterior foi ignorado
               compor("sempre", lit("fim"), run_after=["sucesso", "falhou", "ignorado"])])
    run = sem_docker.executar(f)
    assert estados(run) == {"gatilho": "concluido", "quebra": "falhou", "pulado": "ignorado", "limpeza": "concluido", "sempre": "concluido"}
    assert run["state"] == "falhou"  # a falha de "quebra" nunca foi tratada por um passo que rode após a falha


def test_o_motivo_de_ignorar_explica_a_regra(sem_docker):
    f = fluxo([matematica("quebra", lit(1), lit(0), "dividir"), compor("captura", lit(1), run_after=["sucesso", "ignorado"])])
    motivo = etapas(sem_docker.executar(f))["captura"]["skip_reason"]
    assert "“Operação matemática”" in motivo and "falhou" in motivo and "tiver sucesso ou for ignorado" in motivo


def test_o_primeiro_passo_de_uma_lista_sempre_roda(sem_docker):
    f = fluxo([compor("a", lit(1), run_after=["falhou"])])  # sem anterior: a regra não se aplica
    assert estados(sem_docker.executar(f))["a"] == "concluido"


# ------------------------------------------------------------------ condição
def maior_de_idade(idade):
    return fluxo(
        [condicao("teste", ref("gatilho", "idade"), "maior_igual", "18", label="Maior de idade?",
                  sim=[compor("a", lit("adulto")), saida("sim", "Adulto", ref("a", "resultado"))],
                  nao=[compor("b", lit("menor")), saida("nao", "Menor", ref("b", "resultado"))]),
         compor("depois", lit("fim"))],
        [campo("idade", "numero", idade)])


def test_condicao_verdadeira_executa_so_o_caminho_se_sim(sem_docker):
    run = sem_docker.executar(maior_de_idade(20))
    e = etapas(run)
    assert run["state"] == "concluido" and e["teste"]["outputs"] == {"resultado": True}
    assert [e[k]["state"] for k in ("a", "sim")] == ["concluido"] * 2
    assert [e[k]["state"] for k in ("b", "nao")] == ["ignorado"] * 2
    assert "Se não" in e["nao"]["skip_reason"] and "Maior de idade?" in e["nao"]["skip_reason"]
    assert e["nao"]["inputs"] is None and e["nao"]["outputs"] is None  # nunca executou
    assert valores(run) == {"Adulto": "adulto"}
    assert any("Se sim" in log["text"] for log in e["teste"]["logs"])
    assert e["depois"]["state"] == "concluido"  # o que vem depois da condição roda normalmente


def test_condicao_falsa_executa_so_o_caminho_se_nao(sem_docker):
    run = sem_docker.executar(maior_de_idade(15))
    e = etapas(run)
    assert e["teste"]["outputs"] == {"resultado": False}
    assert [e[k]["state"] for k in ("a", "sim")] == ["ignorado"] * 2 and e["b"]["state"] == "concluido"
    assert valores(run) == {"Menor": "menor"}


@pytest.mark.parametrize("op,comparar,valor,tipo,esperado", [
    ("igual", "5", 5, "numero", True), ("igual", "5", "5", "texto", True), ("diferente", "5", 6, "numero", True),
    ("maior", "5", 6, "numero", True), ("menor", "5", 6, "numero", False), ("menor_igual", "6", 6, "numero", True),
    ("maior", "2,5", 3, "numero", True), ("contem", "ol", "olá", "texto", True), ("nao_contem", "x", "olá", "texto", True),
    ("contem", "2", [1, 2, 3], "lista", True), ("contem", "a", {"a": 1}, "json", True), ("vazio", "", "  ", "texto", True),
    ("nao_vazio", "", [1], "lista", True), ("verdadeiro", "", True, "booleano", True), ("falso", "", True, "booleano", False),
    ("igual", "sim", True, "booleano", True),
])
def test_operadores_da_condicao(sem_docker, op, comparar, valor, tipo, esperado):
    f = fluxo([condicao("c", ref("gatilho", "v"), op, comparar if comparar != "" or op not in ("vazio", "nao_vazio", "verdadeiro", "falso") else None)],
              [campo("v", tipo, valor)])
    assert etapas(sem_docker.executar(f))["c"]["outputs"]["resultado"] is esperado


def test_comparacao_nao_confunde_texto_com_numero_nem_booleano_com_um(sem_docker):
    def rodar(tipo, valor, comparar):
        f = fluxo([condicao("c", ref("gatilho", "v"), "igual", comparar)], [campo("v", tipo, valor)])
        return etapas(sem_docker.executar(f))["c"]["outputs"]["resultado"]

    assert rodar("texto", "5", "5") is True       # texto com texto
    assert rodar("numero", 5, "5.0") is True      # o "5.0" digitado vira número porque o valor é número
    assert rodar("booleano", True, "1") is True   # "1" vale sim para um valor sim/não
    assert rodar("numero", 1, "5") is False
    # um texto que não dá para ler como número, comparado com um número, é um erro explicado (não um "falso" silencioso)
    f = fluxo([condicao("c", ref("gatilho", "v"), "igual", "sim")], [campo("v", "numero", 1)])
    run = sem_docker.executar(f)
    assert run["state"] == "falhou" and "“sim” não é um número válido" in run["error"]["message"]


def test_comparar_com_outro_conteudo_dinamico(sem_docker):
    f = fluxo([condicao("c", ref("gatilho", "a"), "menor", ref("gatilho", "b"))], [campo("a", "numero", 1), campo("b", "numero", 2)])
    assert etapas(sem_docker.executar(f))["c"]["outputs"]["resultado"] is True


def test_varias_condicoes_com_e_ou(sem_docker):
    def rodar(combinador, a):
        regras = [{"esq": ref("gatilho", "n"), "op": "maior", "dir": lit("0")},
                  {"esq": ref("gatilho", "n"), "op": "menor", "dir": lit(str(a))}]
        f = fluxo([passo("c", "builtin.condicao", params={"regras": regras, "combinador": combinador}, slots={"sim": [], "nao": []})],
                  [campo("n", "numero", 5)])
        return etapas(sem_docker.executar(f))["c"]["outputs"]["resultado"]

    assert rodar("e", 10) is True and rodar("e", 3) is False
    assert rodar("ou", 3) is True


def test_condicao_com_tipos_incomparaveis_da_erro_claro(sem_docker):
    f = fluxo([condicao("c", ref("gatilho", "t"), "maior", ref("gatilho", "n"))], [campo("t", "texto", "abc"), campo("n", "numero", 5)])
    run = sem_docker.executar(f)
    assert run["state"] == "falhou" and "Não é possível comparar" in run["error"]["message"]


def test_condicao_aninhada_em_outra(sem_docker):
    interna = condicao("interna", ref("gatilho", "n"), "maior", "10", sim=[compor("grande", lit("grande"))], nao=[compor("medio", lit("médio"))])
    f = fluxo([condicao("externa", ref("gatilho", "n"), "maior", "0", sim=[interna], nao=[compor("neg", lit("negativo"))])], [campo("n", "numero", 5)])
    e = estados(sem_docker.executar(f))
    assert e["medio"] == "concluido" and e["grande"] == "ignorado" and e["neg"] == "ignorado" and e["interna"] == "concluido"
    e2 = estados(sem_docker.executar(f, {"n": -1}))
    assert e2["neg"] == "concluido" and e2["interna"] == "ignorado" and e2["medio"] == "ignorado"  # o filho de um passo ignorado também


# ------------------------------------------------------------------ para cada
def para_cada(itens, corpo, limite=100):
    return fluxo([passo("laco", "builtin.para_cada", {"lista": ref("gatilho", "l")}, {"limite": limite}, slots={"corpo": corpo}),
                  saida("fim", "Quantidade", ref("laco", "quantidade"))], [campo("l", "lista", itens)])


def test_para_cada_repete_o_corpo_registrando_cada_repeticao(sem_docker):
    f = para_cada([10, 20, 30], [matematica("dobro", ref("laco", "item"), lit(2), "multiplicar"), compor("pos", ref("laco", "indice"))])
    run = sem_docker.executar(f)
    assert run["state"] == "concluido" and valores(run) == {"Quantidade": 3}
    r = repeticoes(run, "dobro")
    assert sorted(r) == [(0,), (1,), (2,)]
    assert [r[(i,)]["outputs"]["resultado"] for i in range(3)] == [20, 40, 60]
    assert [repeticoes(run, "pos")[(i,)]["outputs"]["resultado"] for i in range(3)] == [0, 1, 2]
    assert etapas(run)["laco"]["outputs"] == {"quantidade": 3}
    assert "dobro" not in etapas(run)  # passos de laços não têm linha “sem repetição”


def test_para_cada_com_lista_vazia_ignora_o_corpo(sem_docker):
    run = sem_docker.executar(para_cada([], [compor("x", lit(1))]))
    assert run["state"] == "concluido" and etapas(run)["x"]["state"] == "ignorado" and "vazia" in etapas(run)["x"]["skip_reason"]


def test_acima_do_limite_falha_sem_cortar_a_lista_em_silencio(sem_docker):
    run = sem_docker.executar(para_cada(list(range(11)), [compor("x", lit(1))], limite=10))
    assert run["state"] == "falhou"
    erro = etapas(run)["laco"]["error"]
    assert erro["code"] == "limite_itens" and "11 itens" in erro["message"] and "10" in erro["message"]
    assert not repeticoes(run, "x")


def test_falha_em_uma_repeticao_interrompe_as_seguintes_e_aponta_o_passo_de_dentro(sem_docker):
    run = sem_docker.executar(para_cada([2, 0, 5], [matematica("div", lit(10), ref("laco", "item"), "dividir")]))
    assert run["state"] == "falhou"
    assert run["error"]["step_id"] == "div" and "dividir por zero" in run["error"]["message"]
    r = repeticoes(run, "div")
    assert sorted(r) == [(0,), (1,)] and r[(1,)]["state"] == "falhou"  # a terceira nunca começou
    laco = etapas(run)["laco"]
    assert laco["state"] == "falhou" and laco["error"]["code"] == "falha_em_passo_interno"
    assert any("item 2 de 3" in log["text"] for log in laco["logs"])


def test_laco_dentro_de_laco_guarda_as_duas_posicoes(sem_docker):
    interno = passo("interno", "builtin.para_cada", {"lista": ref("externo", "item")}, {"limite": 5},
                    slots={"corpo": [matematica("m", ref("externo", "indice"), ref("interno", "item"), "somar")]})
    f = fluxo([passo("externo", "builtin.para_cada", {"lista": ref("gatilho", "l")}, {"limite": 5}, slots={"corpo": [interno]})],
              [campo("l", "lista", [[1, 2], [10]])])
    run = sem_docker.executar(f)
    assert run["state"] == "concluido"
    assert {k: v["outputs"]["resultado"] for k, v in repeticoes(run, "m").items()} == {(0, 0): 1, (0, 1): 2, (1, 0): 11}


def test_limite_de_registros_por_execucao(sem_docker, monkeypatch):
    monkeypatch.setattr(engine, "MAX_REGISTROS", 5)
    run = sem_docker.executar(para_cada(list(range(10)), [compor("x", lit(1))]))
    assert run["state"] == "falhou" and run["error"]["code"] == "registros_demais"


# ------------------------------------------------------------------ repetir até
def test_repetir_ate_a_condicao_ficar_verdadeira(sem_docker):
    f = fluxo([passo("v", "builtin.var_inicializar", {"inicial": lit(0)}, {"nome": "n", "tipo": "numero"}),
               passo("rep", "builtin.repetir_ate", params={"limite": 10, "combinador": "e", "regras": [{"esq": ref("v", "valor"), "op": "maior_igual", "dir": lit("4")}]},
                     slots={"corpo": [passo("inc", "builtin.var_incrementar", {"quantidade": lit(2)}, {"variavel": "v"})]}),
               saida("s", "Repetições", ref("rep", "repeticoes")), saida("t", "Valor", ref("v", "valor"))])
    assert valores(sem_docker.executar(f)) == {"Repetições": 2, "Valor": 4}


def test_repetir_ate_que_nunca_termina_falha_no_limite(sem_docker):
    f = fluxo([passo("rep", "builtin.repetir_ate", params={"limite": 3, "combinador": "e", "regras": [{"esq": lit("a"), "op": "igual", "dir": lit("b")}]},
                     slots={"corpo": [compor("x", lit(1))]})])
    run = sem_docker.executar(f)
    assert run["state"] == "falhou" and etapas(run)["rep"]["error"]["code"] == "limite_repeticoes"
    assert sorted(repeticoes(run, "x")) == [(0,), (1,), (2,)]


# ------------------------------------------------------------------ escopo (tentar / capturar)
def tentar_capturar(divisor):
    return fluxo(
        [passo("tentar", "builtin.escopo", label="Tentar",
               slots={"corpo": [matematica("div", lit(10), ref("gatilho", "d"), "dividir"), compor("depois", ref("div", "resultado"))]}),
         compor("captura", tpl("Erro: ", ("tentar", "erro")), run_after=["falhou"]),
         saida("s", "Resultado", ref("captura", "resultado"))], [campo("d", "numero", divisor)])


def test_escopo_que_falha_e_capturado_pelo_passo_seguinte(sem_docker):
    run = sem_docker.executar(tentar_capturar(0))
    e = etapas(run)
    assert run["state"] == "concluido"
    assert (e["tentar"]["state"], e["div"]["state"], e["depois"]["state"], e["captura"]["state"]) == ("falhou", "falhou", "ignorado", "concluido")
    assert e["tentar"]["error"]["code"] == "falha_em_passo_interno"
    assert valores(run)["Resultado"].startswith("Erro: Não é possível dividir por zero")
    saidas = e["tentar"]["outputs"]
    assert saidas["falhou"] is True and "dividir por zero" in saidas["erro"]
    assert [(r["estado"], bool(r["erro"])) for r in saidas["resultados"]] == [("falhou", True), ("ignorado", False)]


def test_escopo_sem_falha_pula_o_passo_de_captura(sem_docker):
    run = sem_docker.executar(tentar_capturar(2))
    e = etapas(run)
    assert run["state"] == "concluido" and e["tentar"]["state"] == "concluido" and e["captura"]["state"] == "ignorado"
    assert e["tentar"]["outputs"]["falhou"] is False and e["tentar"]["outputs"]["erro"] == ""
    assert e["s"]["state"] == "ignorado"  # a saída depende do passo de captura


def test_falha_tratada_dentro_do_escopo_nao_faz_o_escopo_falhar(sem_docker):
    f = fluxo([passo("e", "builtin.escopo", slots={"corpo": [matematica("quebra", lit(1), lit(0), "dividir"),
                                                             compor("trata", lit("ok"), run_after=["falhou"])]}),
               compor("depois", lit("segue"))])
    run = sem_docker.executar(f)
    assert run["state"] == "concluido" and estados(run)["e"] == "concluido" and estados(run)["depois"] == "concluido"


def test_falha_dentro_de_condicao_e_de_laco_propaga_para_o_nivel_de_cima(sem_docker):
    f = fluxo([condicao("c", lit(1), "igual", "1", sim=[matematica("quebra", lit(1), lit(0), "dividir")]), compor("depois", lit(1))])
    run = sem_docker.executar(f)
    e = estados(run)
    assert e["c"] == "falhou" and e["depois"] == "ignorado" and run["state"] == "falhou" and run["error"]["step_id"] == "quebra"


# ------------------------------------------------------------------ encerrar
def encerrando(estado, mensagem="Parei aqui"):
    return fluxo([compor("antes", lit(1)),
                  condicao("c", lit(1), "igual", "1", sim=[passo("fim", "builtin.encerrar", {"mensagem": lit(mensagem)}, {"estado": estado})]),
                  compor("depois", lit(2)), saida("s", "x", ref("depois", "resultado"))])


def test_encerrar_com_sucesso_para_o_fluxo_e_marca_o_resto_como_ignorado(sem_docker):
    run = sem_docker.executar(encerrando("sucesso"))
    assert run["state"] == "concluido" and run["result"]["message"] == "Parei aqui"
    e = estados(run)
    assert e["fim"] == "concluido" and e["c"] == "concluido" and e["depois"] == "ignorado" and e["s"] == "ignorado"
    assert "encerrada pelo passo" in etapas(run)["depois"]["skip_reason"]


def test_encerrar_com_falha_e_com_cancelamento(sem_docker):
    falha = sem_docker.executar(encerrando("falha"))
    assert falha["state"] == "falhou" and falha["error"]["code"] == "encerrado_com_falha"
    assert falha["error"]["message"] == "Parei aqui" and falha["error"]["step_id"] == "fim"
    assert sem_docker.executar(encerrando("cancelado"))["state"] == "cancelado"


def test_encerrar_dentro_de_um_escopo_nao_deixa_o_escopo_aberto(sem_docker):
    f = fluxo([passo("e", "builtin.escopo", slots={"corpo": [passo("fim", "builtin.encerrar", {}, {"estado": "sucesso"})]}), compor("depois", lit(1))])
    run = sem_docker.executar(f)
    assert run["state"] == "concluido" and estados(run)["e"] == "concluido" and estados(run)["depois"] == "ignorado"
    assert "executando" not in {s["state"] for s in run["steps"]}


# ------------------------------------------------------------------ variáveis
def test_variaveis_acumulam_valores_em_um_laco(sem_docker):
    f = fluxo([passo("total", "builtin.var_inicializar", {"inicial": lit(0)}, {"nome": "total", "tipo": "numero"}),
               passo("itens", "builtin.var_inicializar", {}, {"nome": "itens", "tipo": "lista"}),
               passo("laco", "builtin.para_cada", {"lista": ref("gatilho", "l")}, {"limite": 10},
                     slots={"corpo": [passo("soma", "builtin.var_incrementar", {"quantidade": ref("laco", "item")}, {"variavel": "total"}),
                                      passo("guarda", "builtin.var_acrescentar", {"valor": ref("laco", "indice")}, {"variavel": "itens"})]}),
               saida("s1", "Total", ref("total", "valor")), saida("s2", "Itens", ref("itens", "valor"))], [campo("l", "lista", [1, 2.5, 4])])
    run = sem_docker.executar(f)
    assert valores(run) == {"Total": 7.5, "Itens": [0, 1, 2]}
    assert etapas(run)["itens"]["outputs"] == {"valor": []}  # a linha da inicialização guarda o valor naquele momento
    assert repeticoes(run, "soma")[(2,)]["outputs"] == {"valor": 7.5}


def test_definir_variavel_e_conferir_o_valor_depois(sem_docker):
    f = fluxo([passo("v", "builtin.var_inicializar", {"inicial": lit("a")}, {"nome": "v", "tipo": "texto"}),
               compor("antes", ref("v", "valor")), passo("d", "builtin.var_definir", {"valor": lit("b")}, {"variavel": "v"}),
               compor("depois", ref("v", "valor")), saida("s1", "antes", ref("antes", "resultado")), saida("s2", "depois", ref("depois", "resultado"))])
    assert valores(sem_docker.executar(f)) == {"antes": "a", "depois": "b"}  # "antes" guardou uma cópia; a variável é lida no momento


def test_variavel_sem_valor_inicial_comeca_com_o_valor_neutro_do_tipo(sem_docker):
    f = fluxo([passo("v", "builtin.var_inicializar", {}, {"nome": "v", "tipo": "lista"}), saida("s", "x", ref("v", "valor"))])
    assert valores(sem_docker.executar(f)) == {"x": []}


def test_definir_variavel_com_tipo_errado_em_tempo_de_execucao(sem_docker):
    f = fluxo([passo("v", "builtin.var_inicializar", {"inicial": lit(1)}, {"nome": "v", "tipo": "numero"}),
               passo("d", "builtin.var_definir", {"valor": ref("gatilho", "t")}, {"variavel": "v"})], [campo("t", "texto", "x")])
    with pytest.raises(ApiError):  # o verificador já recusa (tipo conhecido)
        sem_docker.executar(f)


# ------------------------------------------------------------------ outros blocos de dados
def lista(itens, limite=100, operacao="multiplicar", **extra):
    params = {"operacao": operacao, "operando": 2, "limite": limite, **extra}
    return fluxo([passo("t", "builtin.transformar_lista", {"lista": ref("gatilho", "l")}, params), saida("s", "Resultado", ref("t", "resultado"))],
                 [campo("l", "lista", itens)])


def test_transforma_todos_os_itens_dentro_do_limite(sem_docker):
    run = sem_docker.executar(lista(list(range(1, 51)), limite=50))
    assert valores(run)["Resultado"] == [n * 2 for n in range(1, 51)] and etapas(run)["t"]["outputs"]["quantidade"] == 50


def test_transformar_acima_do_limite_falha_sem_cortar_em_silencio(sem_docker):
    run = sem_docker.executar(lista(list(range(11)), limite=10))
    e = etapas(run)["t"]["error"]
    assert run["state"] == "falhou" and e["code"] == "limite_itens" and "11 itens" in e["message"]


@pytest.mark.parametrize("op,extra,entrada,esperado", [
    ("maiusculas", {}, ["a", "bc"], ["A", "BC"]),
    ("adicionar_texto", {"prefixo": "<", "sufixo": ">"}, ["a", 2], ["<a>", "<2>"]),
    ("para_numero", {}, ["1", "2,5", 3], [1, 2.5, 3]),
    ("extrair_campo", {"campo": "a.b"}, [{"a": {"b": 1}}, {"a": {"b": 2}}], [1, 2]),
    ("somar", {"operando": 10}, [1, 2.5], [11, 12.5]),
])
def test_transformacoes_da_lista(sem_docker, op, extra, entrada, esperado):
    assert valores(sem_docker.executar(lista(entrada, operacao=op, **extra)))["Resultado"] == esperado


def test_item_invalido_indica_a_posicao(sem_docker):
    run = sem_docker.executar(lista([1, "dois", 3]))
    e = etapas(run)["t"]["error"]
    assert run["state"] == "falhou" and "item 2" in e["message"] and e["technical"]["item_index"] == 1


def test_selecionar_campos_caminhos_e_erro_de_campo_ausente(sem_docker):
    dados = {"usuario": {"nome": "Ana", "tags": ["a", "b"]}, "idade": 30}
    f = fluxo([passo("s", "builtin.selecionar_campos", {"objeto": ref("gatilho", "d")}, {"caminhos": "usuario.nome\nusuario.tags.1\nidade"}),
               saida("o", "Tudo", ref("s", "selecionados"))], [campo("d", "json", dados)])
    assert valores(sem_docker.executar(f))["Tudo"] == {"usuario.nome": "Ana", "usuario.tags.1": "b", "idade": 30}
    f["steps"][0]["params"] = {"caminhos": "email"}
    run = sem_docker.executar(f)
    assert run["state"] == "falhou" and "“email” não existe" in run["error"]["message"] and "usuario, idade" in run["error"]["suggestion"]
    f["steps"][0]["params"] = {"caminhos": "email", "se_ausente": "nulo"}
    assert valores(sem_docker.executar(f))["Tudo"] == {"email": None}


def test_transformar_texto(sem_docker):
    def rodar(op, texto="  Olá mundo ", **params):
        f = fluxo([passo("x", "builtin.texto", {"texto": lit(texto)}, {"operacao": op, **params}), saida("s", "r", ref("x", "resultado"))])
        return valores(sem_docker.executar(f))["r"]

    assert rodar("maiusculas") == "  OLÁ MUNDO "
    assert rodar("remover_espacos") == "Olá mundo"
    assert rodar("titulo", "ana maria") == "Ana Maria"
    assert rodar("inverter", "abc") == "cba"
    assert rodar("substituir", "a-b-c", buscar="-", substituir_por="+") == "a+b+c"
    assert rodar("prefixo_sufixo", "x", prefixo="[", sufixo="]") == "[x]"
    f = fluxo([passo("x", "builtin.texto", {"texto": lit("a"), "outro": lit("b")}, {"operacao": "concatenar", "separador": "-"}), saida("s", "r", ref("x", "resultado"))])
    assert valores(sem_docker.executar(f))["r"] == "a-b"


# ------------------------------------------------------------------ tentativas, cancelamento e registro
def test_tentativas_repetem_o_passo_que_falhou_e_registram_nos_logs(sem_docker):
    f = fluxo([matematica("quebra", lit(1), lit(0), "dividir", retry=2)])
    run = sem_docker.executar(f)
    logs = [linha["text"] for linha in etapas(run)["quebra"]["logs"]]
    assert run["state"] == "falhou" and len([t for t in logs if "falhou" in t]) == 2
    assert "Tentativa 1 de 3 falhou" in logs[0] and "Tentativa 2 de 3 falhou" in logs[1]


def test_erros_de_configuracao_nao_sao_repetidos(sem_docker):
    f = fluxo([passo("t", "builtin.transformar_lista", {"lista": lit(list(range(5)))}, {"operacao": "multiplicar", "operando": 2, "limite": 2}, retry=3)])
    assert not etapas(sem_docker.executar(f))["t"]["logs"]  # limite_itens não adianta tentar de novo


def test_cancelar_interrompe_a_espera_entre_tentativas(sem_docker):
    f = fluxo([matematica("quebra", lit(1), lit(0), "dividir", retry=5, intervalo=30), compor("depois", lit(1), run_after=["falhou"])])
    from app.models import Flow
    rid = sem_docker.motor.preparar(Flow.model_validate(f), None)
    t = threading.Thread(target=sem_docker.motor.rodar, args=(rid,))
    inicio = time.monotonic()
    t.start()
    for _ in range(100):  # espera o passo entrar na pausa entre tentativas
        passos = {s["step_id"]: s for s in sem_docker.store.obter_execucao(rid)["steps"]}
        if passos["quebra"]["state"] == "executando" and sem_docker.motor.cancelar(rid):
            break
        time.sleep(0.05)
    t.join(10)
    run = sem_docker.store.obter_execucao(rid)
    assert not t.is_alive() and time.monotonic() - inicio < 8
    assert run["state"] == "cancelado" and run["result"]["message"] == "A execução foi cancelada."
    e = estados(run)
    assert e["quebra"] == "cancelado" and e["depois"] == "ignorado"
    assert sem_docker.motor.cancelar(rid) is False  # já terminou


def test_registro_da_execucao_tem_ids_estados_horarios_e_duracao(sem_docker):
    run = sem_docker.executar(soma())
    assert run["id"].startswith("exe_") and run["kind"] == "fluxo" and run["trigger_inputs"] == {}
    assert run["created_at"] <= run["started_at"] <= run["finished_at"]
    assert isinstance(run["duration_ms"], int) and run["duration_ms"] >= 0
    for e in run["steps"]:
        assert e["state"] == "concluido" and e["started_at"] and e["finished_at"] and e["duration_ms"] is not None
        assert e["logs"] == [] and e["error"] is None and e["iteration"] == []
    assert etapas(run)["soma"]["inputs"] == {"a": 2, "b": 3} and etapas(run)["soma"]["outputs"] == {"resultado": 5}
    assert etapas(run)["gatilho"]["outputs"] == {"a": 2, "b": 3}
    completo = sem_docker.store.obter_execucao(run["id"], com_snapshot=True)["snapshot"]
    assert completo["flow"]["steps"][0]["version"] == 1 and "builtin.matematica@1" in completo["definitions"]


def test_dados_informados_ao_gatilho_ficam_no_registro_da_execucao(sem_docker):
    run = sem_docker.executar(soma(), {"a": 7})
    assert run["trigger_inputs"] == {"a": 7}


# ------------------------------------------------------------------ fluxos inválidos
@pytest.mark.parametrize("construir,codigo", [
    (lambda: fluxo([matematica("m", lit("x"), lit(1))]), "tipo_incompativel"),
    (lambda: fluxo([passo("m", "builtin.matematica")]), "campo_obrigatorio"),
    (lambda: fluxo([passo("s", "builtin.selecionar_campos", {"objeto": lit({})})]), "parametro_invalido"),
    (lambda: fluxo([compor("a", ref("b", "resultado")), compor("b", lit(1))]), "referencia_invalida"),
    (lambda: fluxo([]), "fluxo_vazio"),
])
def test_fluxo_invalido_e_rejeitado_antes_de_executar(sem_docker, construir, codigo):
    with pytest.raises(ApiError) as exc:
        sem_docker.executar(construir())
    assert exc.value.status == 422 and exc.value.codigo == "fluxo_invalido"
    assert codigo in [p["code"] for p in exc.value.problemas]
    with sem_docker.store._conexao() as c:  # nada foi registrado: a execução nem foi criada
        assert c.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_valor_incompativel_em_tempo_de_execucao_e_barrado_pelo_tipo_da_entrada(sem_docker):
    # selecionar_campos.valor é "qualquer": a validação aceita; o valor real é um texto e a soma espera número
    f = fluxo([passo("s", "builtin.selecionar_campos", {"objeto": ref("gatilho", "d")}, {"caminhos": "x"}),
               matematica("m", ref("s", "valor"), lit(1))], [campo("d", "json", {"x": "abc"})])
    run = sem_docker.executar(f)
    e = etapas(run)["m"]["error"]
    assert run["state"] == "falhou" and e["code"] == "entrada_invalida" and "esperava número" in e["message"] and "recebeu texto" in e["message"]


def test_sem_executor_o_fluxo_com_python_e_recusado_e_o_resto_continua_funcionando(sem_docker):
    com_python = fluxo([passo("p", "builtin.transformar_lista", {"lista": lit([1])}, {"operacao": "python", "limite": 5})])
    with pytest.raises(ApiError) as exc:
        sem_docker.executar(com_python)
    problema = next(p for p in exc.value.problemas if p["code"] == "executor_indisponivel")
    assert problema["step_id"] == "p" and "Docker" in problema["message"]
    assert sem_docker.executar(soma())["state"] == "concluido"


def test_teste_isolado_de_bloco_registra_execucao(sem_docker):
    tipo = sem_docker.registro.resolver("builtin.matematica", 1)
    run = sem_docker.motor.testar_bloco(tipo, {"operacao": "multiplicar"}, {"a": 6, "b": 7})
    assert run["kind"] == "bloco" and run["state"] == "concluido" and run["steps"][0]["outputs"] == {"resultado": 42}
    with pytest.raises(ApiError) as exc:
        sem_docker.motor.testar_bloco(tipo, {"operacao": "somar"}, {"a": 1})
    assert "Informe um valor de exemplo" in exc.value.problemas[0]["message"]
    with pytest.raises(ApiError):
        sem_docker.motor.testar_bloco(tipo, {"operacao": "somar"}, {"a": "x", "b": 1})
