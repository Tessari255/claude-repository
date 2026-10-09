"""Verificador de fluxo: referências visíveis, tipos, campos obrigatórios, variáveis e estrutura são checados
ANTES de executar, com mensagens claras. O que é "estrutura" impede salvar; o resto é um rascunho salvável."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Limites
from app.models import Campo, Flow

from .helpers import (
    analisar_dict,
    campo,
    carregar_exemplo,
    codigos,
    compor,
    condicao,
    fluxo,
    lit,
    matematica,
    passo,
    python_inline,
    ref,
    saida,
    tpl,
)


def soma_basica():
    return fluxo(
        [matematica("soma", ref("gatilho", "a"), ref("gatilho", "b")), saida("saida", "Soma", ref("soma", "resultado"))],
        [campo("a", "numero", 2), campo("b", "numero", 3)])


def issue(a, codigo):
    return next(i for i in a.issues if i.code == codigo)


# ------------------------------------------------------------------ o caso feliz
def test_fluxo_correto_nao_tem_problemas():
    a = analisar_dict(soma_basica())
    assert a.issues == []
    assert a.ordem == ["gatilho", "soma", "saida"]


@pytest.mark.parametrize("arquivo", ["01-saudacao.json", "02-soma.json", "03-condicao.json", "04-lista.json",
                                     "05-tratar-erros.json", "06-laco-e-variavel.json"])
def test_os_modelos_entregues_sao_validos(arquivo):
    a = analisar_dict(carregar_exemplo(arquivo)["flow"])
    assert a.erros == [], [i.message for i in a.issues]


# ------------------------------------------------------------------ conteúdo dinâmico: visibilidade
def test_nao_pode_usar_o_conteudo_de_um_passo_que_roda_depois():
    f = fluxo([compor("a", ref("b", "resultado")), compor("b", lit(1))])
    i = issue(analisar_dict(f), "referencia_invalida")
    assert i.step_id == "a" and i.field == "entrada" and i.scope == "configuracao"
    assert "só roda depois" in i.message


def test_um_passo_nao_pode_usar_o_proprio_conteudo():
    assert "referencia_invalida" in codigos(analisar_dict(fluxo([compor("a", ref("a", "resultado"))])))


def test_passo_apagado_deixa_a_referencia_invalida_mas_o_rascunho_continua_salvavel():
    a = analisar_dict(fluxo([compor("a", ref("apagado", "resultado"))]))
    i = issue(a, "referencia_invalida")
    assert "não existe mais" in i.message and a.erros_de_estrutura == []


def test_saida_inexistente_em_um_passo_que_existe():
    a = analisar_dict(fluxo([compor("a", lit(1)), compor("b", ref("a", "nada"))]))
    assert "saida_inexistente" in codigos(a)


def test_conteudo_de_dentro_de_uma_condicao_nao_fica_visivel_depois_dela():
    f = fluxo([condicao("c", lit(1), "igual", "1", sim=[compor("dentro", lit("x"))]), compor("fora", ref("dentro", "resultado"))])
    assert "referencia_invalida" in codigos(analisar_dict(f))


def test_dentro_do_mesmo_ramo_e_antes_da_condicao_o_conteudo_e_visivel():
    f = fluxo([compor("antes", lit(1)),
               condicao("c", ref("antes", "resultado"), "igual", "1",
                        sim=[compor("a", ref("antes", "resultado")), compor("b", ref("a", "resultado"))])])
    assert analisar_dict(f).erros == []


def test_conteudo_de_dentro_de_um_escopo_continua_visivel_depois_dele():
    f = fluxo([passo("e", "builtin.escopo", slots={"corpo": [compor("dentro", lit("x"))]}), compor("fora", ref("dentro", "resultado"))])
    assert analisar_dict(f).erros == []


def test_irmao_de_outro_ramo_nao_e_visivel():
    f = fluxo([condicao("c", lit(1), "igual", "1", sim=[compor("a", lit(1))], nao=[compor("b", ref("a", "resultado"))])])
    assert "referencia_invalida" in codigos(analisar_dict(f))


def test_item_do_laco_so_existe_dentro_do_laco():
    def laco(corpo):
        return passo("p", "builtin.para_cada", {"lista": ref("gatilho", "l")}, {"limite": 10}, slots={"corpo": corpo})
    dentro = fluxo([laco([compor("x", ref("p", "item"))])], [campo("l", "lista", [1])])
    assert analisar_dict(dentro).erros == []
    fora = fluxo([laco([]), compor("x", ref("p", "item"))], [campo("l", "lista", [1])])
    a = analisar_dict(fora)
    assert "saida_inexistente" in codigos(a) and "só existe dentro" in issue(a, "saida_inexistente").message
    # e a quantidade só existe depois que o laço termina
    ruim = fluxo([laco([compor("x", ref("p", "quantidade"))])], [campo("l", "lista", [1])])
    assert "saida_inexistente" in codigos(analisar_dict(ruim))
    bom = fluxo([laco([]), compor("x", ref("p", "quantidade"))], [campo("l", "lista", [1])])
    assert analisar_dict(bom).erros == []


def test_o_gatilho_e_visivel_em_qualquer_lugar_inclusive_aninhado():
    f = fluxo([condicao("c", lit(1), "igual", "1", sim=[passo("e", "builtin.escopo", slots={"corpo": [compor("x", ref("gatilho", "nome"))]})])],
              [campo("nome")])
    assert analisar_dict(f).erros == []


# ------------------------------------------------------------------ tipos
def test_texto_em_entrada_numerica_e_recusado():
    f = fluxo([matematica("m", ref("gatilho", "t"), lit(1))], [campo("t", "texto", "x")])
    i = issue(analisar_dict(f), "tipo_incompativel")
    assert i.field == "a" and "texto" in i.message and "número" in i.message


def test_texto_misturado_com_conteudo_dinamico_vira_texto_e_nao_serve_para_numero():
    f = fluxo([matematica("m", tpl("5", ("gatilho", "n")), lit(1))], [campo("n", "numero", 1)])
    i = issue(analisar_dict(f), "tipo_incompativel")
    assert "mistura texto com conteúdo dinâmico" in i.message


def test_valor_fixo_com_tipo_errado():
    a = analisar_dict(fluxo([matematica("m", lit("abc"), lit(1))]))
    assert "tipo_incompativel" in codigos(a)


def test_saida_qualquer_e_aceita_e_verificada_so_em_tempo_de_execucao():
    f = fluxo([passo("s", "builtin.selecionar_campos", {"objeto": ref("gatilho", "d")}, {"caminhos": "x"}),
               matematica("m", ref("s", "valor"), lit(1))], [campo("d", "json", {"x": 1})])
    assert "tipo_incompativel" not in codigos(analisar_dict(f))


def test_compor_herda_o_tipo_do_campo_e_a_variavel_o_do_parametro():
    f = fluxo([compor("c", lit(7)), passo("v", "builtin.var_inicializar", {"inicial": lit([1])}, {"nome": "v", "tipo": "lista"}),
               matematica("m", ref("c", "resultado"), lit(1))])
    a = analisar_dict(f)
    assert a.port_types["c"]["outputs"]["resultado"] == "numero"
    assert a.port_types["v"]["outputs"]["valor"] == "lista"
    assert a.port_types["v"]["inputs"]["inicial"] == "lista"  # o tipo do campo acompanha o parâmetro
    assert "tipo_incompativel" not in codigos(a)


def test_caminho_dentro_do_conteudo_so_vale_para_objetos_e_listas():
    f = fluxo([compor("a", ref("gatilho", "n", "x.y"))], [campo("n", "numero", 1)])
    assert "tipo_incompativel" in codigos(analisar_dict(f))
    ok = fluxo([matematica("a", ref("gatilho", "d", "x.y"), lit(1))], [campo("d", "json", {"x": {"y": 1}})])
    assert analisar_dict(ok).erros == []


# ------------------------------------------------------------------ campos e parâmetros
def test_campos_obrigatorios_ausentes_sao_problemas_de_configuracao():
    f = fluxo([passo("t", "builtin.texto", params={"operacao": "substituir"}), passo("s", "builtin.selecionar_campos")])
    a = analisar_dict(f)
    cfg = {(i.code, i.step_id, i.field) for i in a.issues if i.scope == "configuracao" and i.severity == "erro"}
    assert ("campo_obrigatorio", "t", "texto") in cfg
    assert ("parametro_invalido", "t", "buscar") in cfg
    assert ("parametro_invalido", "s", "caminhos") in cfg
    assert ("campo_obrigatorio", "s", "objeto") in cfg
    assert a.erros_de_estrutura == []  # rascunho salvável


def test_texto_vazio_conta_como_nao_preenchido_mas_conteudo_dinamico_vazio_nao():
    a = analisar_dict(fluxo([passo("t", "builtin.texto", {"texto": lit("")})]))
    assert ("campo_obrigatorio", "t") in {(i.code, i.step_id) for i in a.issues}
    b = analisar_dict(fluxo([passo("t", "builtin.texto", {"texto": ref("gatilho", "x")})], [campo("x")]))
    assert "campo_obrigatorio" not in codigos(b)


def test_campo_opcional_pode_ficar_vazio_e_parametro_so_e_exigido_quando_visivel():
    a = analisar_dict(fluxo([passo("t", "builtin.texto", {"texto": lit("a")}, {"operacao": "maiusculas"})]))
    assert a.erros == [] or all(i.field != "buscar" for i in a.issues)
    assert not any(i.field in ("buscar", "outro") for i in a.issues)


def test_parametro_com_tipo_errado_e_fora_da_faixa():
    f = fluxo([passo("p", "builtin.transformar_lista", {"lista": lit([1])}, {"operacao": "multiplicar", "operando": "dois", "limite": 999999})])
    msgs = " ".join(i.message for i in analisar_dict(f).issues if i.field)
    assert "precisa ser um número" in msgs and "no máximo" in msgs


def test_campo_de_entrada_desconhecido_e_so_um_aviso():
    a = analisar_dict(fluxo([compor("c", lit(1))]) | {"steps": [passo("c", "builtin.compor", {"entrada": lit(1), "fantasma": lit(2)})]})
    assert issue(a, "entrada_desconhecida").severity == "aviso" and a.erros == []


def test_parametro_desconhecido_e_so_um_aviso():
    a = analisar_dict(fluxo([passo("c", "builtin.compor", {"entrada": lit(1)}, {"antigo": 1})]))
    assert issue(a, "parametro_desconhecido").severity == "aviso"


# ------------------------------------------------------------------ estrutura
def test_passo_duplicado_e_erro_de_estrutura():
    a = analisar_dict(fluxo([compor("a", lit(1)), compor("a", lit(2))]))
    assert issue(a, "passo_duplicado").scope == "estrutura"
    f = fluxo([compor("gatilho", lit(1))])  # o id do gatilho também conta
    assert "passo_duplicado" in codigos(analisar_dict(f), scope="estrutura")


def test_espaco_inexistente_e_erro_de_estrutura():
    f = fluxo([passo("c", "builtin.compor", {"entrada": lit(1)}, slots={"corpo": [compor("x", lit(1))]})])
    assert "espaco_invalido" in codigos(analisar_dict(f), scope="estrutura")


def test_gatilho_so_pode_ser_gatilho_e_passo_nao_pode_ser_gatilho():
    f = fluxo([passo("g2", "builtin.gatilho_manual", params={"campos": []})])
    assert "passo_invalido" in codigos(analisar_dict(f), scope="estrutura")
    ruim = fluxo([]) | {"trigger": passo("gatilho", "builtin.compor", {"entrada": lit(1)})}
    assert "gatilho_invalido" in codigos(analisar_dict(ruim), scope="estrutura")
    outro_id = fluxo([]) | {"trigger": gatilho_com_id("outro")}
    assert "gatilho_invalido" in codigos(analisar_dict(outro_id), scope="estrutura")


def gatilho_com_id(id):
    return {"id": id, "type": "builtin.gatilho_manual", "version": 1, "params": {"campos": []}}


def test_bloco_desconhecido_e_configuracao_e_nao_gera_erros_em_cascata():
    f = fluxo([passo("x", "custom.nao_existe"), compor("c", ref("x", "qualquer_coisa"))])
    a = analisar_dict(f)
    assert codigos(a, severity="erro").count("bloco_desconhecido") == 1
    assert "referencia_invalida" not in codigos(a) and "saida_inexistente" not in codigos(a)
    assert a.erros_de_estrutura == []


def test_limites_do_formato_sao_checados_na_modelagem():
    def aninhado(n):
        p = compor("folha", lit(1))
        for i in range(n):
            p = passo(f"e{i}", "builtin.escopo", slots={"corpo": [p]})
        return p

    Flow.model_validate(fluxo([aninhado(7)]))  # 8 níveis no total: permitido
    with pytest.raises(ValidationError, match="aninhados demais"):
        Flow.model_validate(fluxo([aninhado(8)]))
    with pytest.raises(ValidationError):
        Flow.model_validate(fluxo([compor(f"c{i}", lit(1)) for i in range(201)]))


def test_campo_tem_valor_fixo_ou_conteudo_dinamico_nunca_os_dois_nem_nenhum():
    assert Campo.model_validate({"value": None}).value is None  # nulo explícito é permitido
    assert Campo.model_validate({"parts": []}).dinamico
    for ruim in ({}, {"value": 1, "parts": []}, {"value": float("nan")}):
        with pytest.raises(ValidationError):
            Campo.model_validate(ruim)
    # relê igual ao que gravou (só a chave ativa)
    assert Campo(parts=["a"]).model_dump() == {"parts": ["a"]} and Campo(value=1).model_dump() == {"value": 1}


# ------------------------------------------------------------------ variáveis
def var(id, nome, tipo="numero", inicial=None):
    return passo(id, "builtin.var_inicializar", {"inicial": lit(inicial)} if inicial is not None else {}, {"nome": nome, "tipo": tipo})


def test_variaveis_so_na_lista_principal():
    f = fluxo([condicao("c", lit(1), "igual", "1", sim=[var("v", "x")])])
    assert "variavel_fora_do_topo" in codigos(analisar_dict(f))


def test_nome_de_variavel_repetido():
    a = analisar_dict(fluxo([var("a", "Total"), var("b", "total")]))
    assert issue(a, "variavel_duplicada").step_id == "b"


def test_definir_variavel_exige_variavel_existente_e_anterior():
    def definir(variavel):
        return passo("d", "builtin.var_definir", {"valor": lit(1)}, {"variavel": variavel})
    assert "variavel_invalida" in codigos(analisar_dict(fluxo([definir("")])))
    assert "variavel_invalida" in codigos(analisar_dict(fluxo([definir("v")])))  # nem existe
    assert "variavel_invalida" in codigos(analisar_dict(fluxo([definir("v"), var("v", "x")])))  # criada depois
    assert analisar_dict(fluxo([var("v", "x"), definir("v")])).erros == []


def test_tipos_da_variavel_sao_conferidos():
    inc = passo("i", "builtin.var_incrementar", {"quantidade": lit(1)}, {"variavel": "v"})
    assert "variavel_invalida" in codigos(analisar_dict(fluxo([var("v", "x", "texto"), inc])))
    acr = passo("a", "builtin.var_acrescentar", {"valor": lit(1)}, {"variavel": "v"})
    assert "variavel_invalida" in codigos(analisar_dict(fluxo([var("v", "x", "numero"), acr])))
    assert analisar_dict(fluxo([var("v", "x", "lista"), acr])).erros == []
    definir_errado = passo("d", "builtin.var_definir", {"valor": lit("texto")}, {"variavel": "v"})
    assert "tipo_incompativel" in codigos(analisar_dict(fluxo([var("v", "x", "numero"), definir_errado])))


def test_valor_inicial_acompanha_o_tipo_escolhido_para_a_variavel():
    assert "tipo_incompativel" in codigos(analisar_dict(fluxo([var("v", "x", "numero", "texto")])))
    assert analisar_dict(fluxo([var("v", "x", "texto", "olá")])).erros == []


# ------------------------------------------------------------------ condições e regras
def test_regras_vazias_ou_incompletas():
    assert "parametro_invalido" in codigos(analisar_dict(fluxo([passo("c", "builtin.condicao", params={"regras": []}, slots={"sim": [], "nao": []})])))
    sem_valor = condicao("c", lit(""), "igual", "1")
    assert "campo_obrigatorio" in codigos(analisar_dict(fluxo([sem_valor])))
    sem_dir = passo("c", "builtin.condicao", params={"regras": [{"esq": lit(1), "op": "igual"}]}, slots={"sim": [], "nao": []})
    assert "campo_obrigatorio" in codigos(analisar_dict(fluxo([sem_dir])))
    op_ruim = passo("c", "builtin.condicao", params={"regras": [{"esq": lit(1), "op": "parecido", "dir": lit(1)}]}, slots={"sim": [], "nao": []})
    assert "parametro_invalido" in codigos(analisar_dict(fluxo([op_ruim])))


def test_testes_unarios_nao_pedem_valor_de_comparacao():
    f = fluxo([condicao("c", ref("gatilho", "t"), "vazio")], [campo("t")])
    assert analisar_dict(f).erros == []


def test_comparacao_numerica_exige_numero_valido():
    f = fluxo([condicao("c", ref("gatilho", "n"), "maior", "abc")], [campo("n", "numero", 1)])
    assert issue(analisar_dict(f), "parametro_invalido").field == "regras"
    ok = fluxo([condicao("c", ref("gatilho", "n"), "maior", "2,5")], [campo("n", "numero", 1)])
    assert analisar_dict(ok).erros == []


def test_teste_de_sim_ou_nao_exige_valor_booleano():
    f = fluxo([condicao("c", ref("gatilho", "n"), "verdadeiro")], [campo("n", "numero", 1)])
    assert "tipo_incompativel" in codigos(analisar_dict(f))


def test_regra_com_referencia_invalida_e_apontada_no_campo_das_regras():
    f = fluxo([condicao("c", ref("depois", "resultado"), "igual", "1"), compor("depois", lit(1))])
    i = issue(analisar_dict(f), "referencia_invalida")
    assert i.step_id == "c" and i.field == "regras"


# ------------------------------------------------------------------ python inline
def test_python_inline_expande_entradas_e_saidas_declaradas():
    f = fluxo([python_inline("p", "def run(inputs, params):\n    return {'x': 1}", {"nome": ("texto", ref("gatilho", "n"))}, {"x": "numero"}),
               matematica("m", ref("p", "x"), lit(1))], [campo("n")])
    a = analisar_dict(f)
    assert a.erros == []
    assert a.port_types["p"]["outputs"] == {"x": "numero"} and a.port_types["p"]["inputs"] == {"nome": "texto"}


def test_python_inline_exige_saida_e_ids_validos_e_unicos():
    sem_saidas = fluxo([python_inline("p", "def run(i, p):\n    return {}", saidas={})])
    assert "parametro_invalido" in codigos(analisar_dict(sem_saidas))
    ruim = passo("p", "builtin.python", params={"entradas": [{"id": "Nome Ruim", "label": "x", "type": "texto"}],
                                                "saidas": [{"id": "r", "label": "R", "type": "texto"}], "codigo": "x"})
    a = analisar_dict(fluxo([ruim]))
    assert any(i.field == "entradas" for i in a.issues)
    repetidas = passo("p", "builtin.python", params={"entradas": [], "codigo": "x",
                                                     "saidas": [{"id": "r", "label": "R", "type": "texto"}, {"id": "r", "label": "S", "type": "texto"}]})
    assert any("repetidos" in i.message for i in analisar_dict(fluxo([repetidas])).issues)


def test_gatilho_com_campos_repetidos_ou_padrao_de_tipo_errado():
    f = fluxo([], [campo("a", "texto", label="Nome"), campo("b", "texto", label="nome")])
    assert any("dois campos" in i.message for i in analisar_dict(f).issues)
    g = fluxo([], [campo("n", "numero", "oi")])
    assert any("valor padrão" in i.message for i in analisar_dict(g).issues)


# ------------------------------------------------------------------ fluxo como um todo
def test_fluxo_vazio_e_aviso_de_sem_saida():
    assert "fluxo_vazio" in codigos(analisar_dict(fluxo([])))
    a = analisar_dict(fluxo([compor("a", lit(1))]))
    aviso = issue(a, "sem_saida")
    assert aviso.severity == "aviso" and a.erros == []


def test_saida_final_nao_pode_ficar_dentro_de_um_laco():
    laco = passo("p", "builtin.para_cada", {"lista": lit([1])}, {"limite": 5}, slots={"corpo": [saida("s", "x", lit(1))]})
    assert "saida_em_laco" in codigos(analisar_dict(fluxo([laco])))
    em_condicao = condicao("c", lit(1), "igual", "1", sim=[saida("s", "x", lit(1))])
    assert "saida_em_laco" not in codigos(analisar_dict(fluxo([em_condicao])))


def test_executor_indisponivel_bloqueia_so_os_passos_com_codigo_python():
    class Fora:
        disponivel = False
        mensagem = "O Docker não foi encontrado neste computador."
        instrucao = "Instale o Docker."

    f = fluxo([passo("t1", "builtin.transformar_lista", {"lista": lit([1])}, {"operacao": "python", "limite": 10}),
               passo("t2", "builtin.transformar_lista", {"lista": lit([1])}, {"operacao": "multiplicar", "operando": 2, "limite": 10}),
               python_inline("py", "def run(i, p):\n    return {'resultado': 'x'}")])
    a = analisar_dict(f, sandbox=Fora())
    assert {i.step_id for i in a.issues if i.code == "executor_indisponivel"} == {"t1", "py"}


def test_tempo_limite_do_passo_nao_passa_do_limite_do_servidor():
    f = fluxo([python_inline("p", "def run(i, p):\n    return {'resultado': 'x'}", timeout=30)])
    a = analisar_dict(f, limites=Limites(tempo_s=10))
    assert issue(a, "tempo_limite_invalido").field == "timeout_s"
    ok = fluxo([python_inline("p", "def run(i, p):\n    return {'resultado': 'x'}", timeout=5)])
    assert "tempo_limite_invalido" not in codigos(analisar_dict(ok, limites=Limites(tempo_s=10)))


def test_ordem_de_documento_percorre_ramos_e_corpos():
    f = fluxo([compor("a", lit(1)), condicao("c", lit(1), "igual", "1", sim=[compor("s", lit(1))], nao=[compor("n", lit(1))]), compor("z", lit(1))])
    assert analisar_dict(f).ordem == ["gatilho", "a", "c", "s", "n", "z"]
