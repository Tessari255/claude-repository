"""Critério 6 e regras de estrutura: entradas incompatíveis, campos obrigatórios ausentes e ciclos
são rejeitados ANTES da execução, com mensagens claras."""

from __future__ import annotations

from app.models import Connection, Flow
from app.validation import ordem_topologica, verificar_conexao

from .helpers import (analisar_dict, bloco, codigos, con, constante, fluxo, resolver_builtin, saida,
                      carregar_exemplo)


def soma_basica():
    return fluxo(
        [constante("a", "numero", 2), constante("b", "numero", 3),
         bloco("soma", "builtin.matematica", {"operacao": "somar"}), saida()],
        [con("c1", "a", "valor", "soma", "a"), con("c2", "b", "valor", "soma", "b"),
         con("c3", "soma", "resultado", "saida", "valor")])


def test_fluxo_correto_nao_tem_problemas():
    a = analisar_dict(soma_basica())
    assert a.issues == []
    assert a.order is not None and a.order.index("soma") > a.order.index("a")


def test_ciclo_e_rejeitado_com_mensagem_que_nomeia_os_blocos():
    f = fluxo(
        [bloco("m1", "builtin.matematica", label="Primeira conta"), bloco("m2", "builtin.matematica", label="Segunda conta")],
        [con("c1", "m1", "resultado", "m2", "a"), con("c2", "m2", "resultado", "m1", "a")])
    a = analisar_dict(f)
    ciclo = [i for i in a.issues if i.code == "ciclo"]
    assert len(ciclo) == 1 and ciclo[0].scope == "estrutura" and ciclo[0].severity == "erro"
    assert "Primeira conta" in ciclo[0].message and "Segunda conta" in ciclo[0].message
    assert "ciclo" in ciclo[0].message
    assert set(ciclo[0].connection_ids) == {"c1", "c2"}
    assert a.order is None  # não há ordem de execução possível


def test_bloco_ligado_a_si_mesmo_e_um_ciclo():
    f = fluxo([bloco("m", "builtin.matematica")], [con("c", "m", "resultado", "m", "a")])
    assert "ciclo" in codigos(analisar_dict(f))


def test_ciclo_longo_de_tres_blocos():
    f = fluxo([bloco(i, "builtin.matematica") for i in ("x", "y", "z")],
              [con("1", "x", "resultado", "y", "a"), con("2", "y", "resultado", "z", "a"), con("3", "z", "resultado", "x", "a")])
    assert codigos(analisar_dict(f)).count("ciclo") == 1


def test_nova_conexao_que_fecha_ciclo_e_recusada_no_momento_de_conectar():
    f = Flow.model_validate(fluxo(
        [bloco("m1", "builtin.matematica"), bloco("m2", "builtin.matematica")],
        [con("c1", "m1", "resultado", "m2", "a")]))
    nova = Connection.model_validate(con("c2", "m2", "resultado", "m1", "b"))
    problemas = verificar_conexao(f, nova, resolver_builtin)
    assert [p.code for p in problemas] == ["ciclo"]
    ok = Connection.model_validate(con("c3", "m1", "resultado", "m2", "b"))
    assert verificar_conexao(f, ok, resolver_builtin) == []


def test_tipos_incompativeis_sao_rejeitados():
    # saída de texto ligada a uma entrada numérica
    f = fluxo([constante("t", "texto", "olá"), bloco("m", "builtin.matematica")],
              [con("c1", "t", "valor", "m", "a")])
    a = analisar_dict(f)
    erro = next(i for i in a.issues if i.code == "tipo_incompativel")
    assert erro.scope == "estrutura" and erro.connection_id == "c1" and erro.block_id == "m"
    assert "texto" in erro.message and "número" in erro.message


def test_tipo_da_constante_acompanha_o_parametro_tipo():
    f = fluxo([constante("n", "numero", 7), bloco("m", "builtin.matematica")], [con("c1", "n", "valor", "m", "a")])
    a = analisar_dict(f)
    assert "tipo_incompativel" not in codigos(a)
    assert a.port_types["n"]["outputs"]["valor"] == "numero"


def test_saida_qualquer_e_verificada_em_tempo_de_execucao_nao_na_validacao():
    # selecionar_campos.valor é "qualquer": a conexão é aceita (o tipo real só existe na execução)
    f = fluxo([bloco("i", "builtin.inicio", {"dados": {"x": 1}}), bloco("s", "builtin.selecionar_campos", {"caminhos": "x"}),
               bloco("m", "builtin.matematica")],
              [con("c1", "i", "dados", "s", "objeto"), con("c2", "s", "valor", "m", "a")])
    assert "tipo_incompativel" not in codigos(analisar_dict(f))


def test_condicao_herda_o_tipo_da_entrada_e_barra_incompatibilidade():
    f = fluxo([constante("n", "numero", 5), bloco("c", "builtin.condicao", {"operador": "maior", "comparar_com": "1"}),
               bloco("t", "builtin.texto")],
              [con("c1", "n", "valor", "c", "valor"), con("c2", "c", "verdadeiro", "t", "texto")])
    a = analisar_dict(f)
    assert a.port_types["c"]["outputs"]["verdadeiro"] == "numero"
    assert "tipo_incompativel" in codigos(a)  # número não entra em entrada de texto


def test_entrada_aceita_uma_unica_conexao():
    f = fluxo([constante("a", "numero", 1), constante("b", "numero", 2), bloco("m", "builtin.matematica")],
              [con("c1", "a", "valor", "m", "a"), con("c2", "b", "valor", "m", "a")])
    a = analisar_dict(f)
    dup = next(i for i in a.issues if i.code == "entrada_duplicada")
    assert dup.connection_id == "c2" and dup.scope == "estrutura"


def test_saida_pode_alimentar_varios_blocos():
    f = fluxo([constante("a", "numero", 4), bloco("m1", "builtin.matematica"), bloco("m2", "builtin.matematica")],
              [con("c1", "a", "valor", "m1", "a"), con("c2", "a", "valor", "m2", "a")])
    assert "entrada_duplicada" not in codigos(analisar_dict(f))


def test_campos_obrigatorios_ausentes_sao_problemas_de_configuracao():
    f = fluxo([bloco("t", "builtin.texto", {"operacao": "substituir"}), bloco("s", "builtin.selecionar_campos", {})])
    a = analisar_dict(f)
    cfg = [(i.code, i.block_id, i.param or i.port) for i in a.issues if i.scope == "configuracao" and i.severity == "erro"]
    assert ("entrada_obrigatoria", "t", "texto") in cfg      # entrada obrigatória sem ligação
    assert ("parametro_invalido", "t", "buscar") in cfg      # parâmetro obrigatório da operação escolhida
    assert ("parametro_invalido", "s", "caminhos") in cfg
    assert ("entrada_obrigatoria", "s", "objeto") in cfg
    assert a.erros_de_estrutura == []  # rascunho salvável


def test_parametro_so_e_exigido_quando_visivel():
    f = fluxo([bloco("t", "builtin.texto", {"operacao": "maiusculas"})])
    a = analisar_dict(f)
    assert not any(i.param == "buscar" for i in a.issues)


def test_parametro_com_tipo_errado_e_fora_da_faixa():
    f = fluxo([bloco("p", "builtin.para_cada", {"operacao": "multiplicar", "operando": "dois", "limite": 999999})])
    msgs = " ".join(i.message for i in analisar_dict(f).issues if i.param)
    assert "precisa ser um número" in msgs and "no máximo" in msgs


def test_constante_valida_o_valor_conforme_o_tipo():
    ruim = analisar_dict(fluxo([constante("c", "numero", "abc")]))
    assert any(i.code == "parametro_invalido" and i.param == "valor" for i in ruim.issues)
    bom = analisar_dict(fluxo([constante("c", "lista", [1, 2]), constante("d", "booleano", False), constante("e", "texto", "")]))
    assert not any(i.code == "parametro_invalido" for i in bom.issues)


def test_bloco_ou_porta_inexistente():
    f = fluxo([constante("a", "numero", 1), bloco("m", "builtin.matematica")],
              [con("c1", "a", "valor", "m", "inexistente"), con("c2", "a", "valor", "fantasma", "a")])
    assert codigos(analisar_dict(f)).count("conexao_invalida") == 2


def test_tipo_de_bloco_desconhecido():
    a = analisar_dict(fluxo([bloco("x", "custom.nao_existe")]))
    assert "bloco_desconhecido" in codigos(a, scope="estrutura")


def test_uniao_de_caminhos_condicionais_da_mesma_condicao_e_recusada():
    f = fluxo([constante("n", "numero", 5), bloco("c", "builtin.condicao", {"operador": "maior", "comparar_com": "1"}),
               bloco("m", "builtin.matematica"), constante("z", "numero", 1)],
              [con("c1", "n", "valor", "c", "valor"), con("c2", "c", "verdadeiro", "m", "a"), con("c3", "c", "falso", "m", "b")])
    j = next(i for i in analisar_dict(f).issues if i.code == "juncao_condicional")
    assert j.scope == "estrutura" and j.block_id == "m"
    assert "não é possível" in j.message and set(j.connection_ids) == {"c2", "c3"}


def test_uniao_de_condicoes_diferentes_tambem_e_recusada():
    f = fluxo([constante("n", "numero", 5),
               bloco("c1", "builtin.condicao", {"operador": "maior", "comparar_com": "1"}),
               bloco("c2", "builtin.condicao", {"operador": "menor", "comparar_com": "9"}),
               bloco("m", "builtin.matematica")],
              [con("k1", "n", "valor", "c1", "valor"), con("k2", "n", "valor", "c2", "valor"),
               con("k3", "c1", "verdadeiro", "m", "a"), con("k4", "c2", "verdadeiro", "m", "b")])
    assert "juncao_condicional" in codigos(analisar_dict(f))


def test_misturar_dado_comum_com_um_caminho_condicional_e_permitido():
    a = analisar_dict(carregar_exemplo("03-condicao.json")["flow"], resolver=lambda i, v: resolver_builtin(i, v))
    assert "juncao_condicional" not in codigos(a)
    assert a.erros == []


def test_ordem_segue_dependencias_e_nao_a_posicao_visual():
    # Lista em ordem invertida e com posições embaralhadas: a ordem de execução continua válida.
    f = fluxo(
        [saida("saida", x=-500, y=900), bloco("soma", "builtin.matematica", {"operacao": "somar"}, x=900, y=-400),
         constante("b", "numero", 3, x=300, y=300), constante("a", "numero", 2, x=-900, y=0)],
        [con("c1", "a", "valor", "soma", "a"), con("c2", "b", "valor", "soma", "b"),
         con("c3", "soma", "resultado", "saida", "valor")])
    ordem = analisar_dict(f).order
    assert ordem.index("a") < ordem.index("soma") < ordem.index("saida")
    assert ordem.index("b") < ordem.index("soma")
    assert ordem_topologica(["x"], []) == ["x"]


def test_fluxo_vazio_e_aviso_de_sem_saida():
    assert "fluxo_vazio" in codigos(analisar_dict(fluxo([])))
    a = analisar_dict(fluxo([constante("a", "numero", 1)]))
    aviso = next(i for i in a.issues if i.code == "sem_saida")
    assert aviso.severity == "aviso" and a.erros == []


def test_executor_indisponivel_bloqueia_blocos_com_codigo_python():
    class Fora:
        disponivel = False
        mensagem = "O Docker não foi encontrado neste computador."
        instrucao = "Instale o Docker."

    f = fluxo([bloco("p", "builtin.para_cada", {"operacao": "python", "limite": 10}),
               bloco("q", "builtin.para_cada", {"operacao": "multiplicar", "operando": 2, "limite": 10})])
    a = analisar_dict(f, sandbox=Fora())
    bloqueados = {i.block_id for i in a.issues if i.code == "executor_indisponivel"}
    assert bloqueados == {"p"}  # só o bloco que usa Python
