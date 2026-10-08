"""Motor de execução com blocos internos (não precisa de Docker): critérios 3, 4 e 5, ordem,
estados, diagnóstico por bloco e parada na primeira falha."""

from __future__ import annotations

import pytest

from app.errors import ApiError

from .helpers import bloco, con, constante, etapas, fluxo, saida


def soma(a=2, b=3, operacao="somar"):
    return fluxo(
        [constante("a", "numero", a), constante("b", "numero", b),
         bloco("soma", "builtin.matematica", {"operacao": operacao}), saida("saida", "Soma")],
        [con("c1", "a", "valor", "soma", "a"), con("c2", "b", "valor", "soma", "b"),
         con("c3", "soma", "resultado", "saida", "valor")])


def test_criterio_3_dois_valores_constantes_e_uma_soma(sem_docker):
    run = sem_docker.executar(soma(2, 3))
    assert run["state"] == "concluido"
    assert run["result"]["outputs"] == [{"block_id": "saida", "title": "Soma", "value": 5}]
    assert {e["state"] for e in run["steps"]} == {"concluido"}


@pytest.mark.parametrize("op,a,b,esperado", [
    ("subtrair", 10, 4, 6), ("multiplicar", 6, 7, 42), ("dividir", 9, 3, 3), ("dividir", 7, 2, 3.5),
    ("resto", 10, 4, 2), ("potencia", 2, 10, 1024), ("minimo", 3, 8, 3), ("maximo", 3, 8, 8),
])
def test_operacoes_matematicas(sem_docker, op, a, b, esperado):
    run = sem_docker.executar(soma(a, b, op))
    assert run["result"]["outputs"][0]["value"] == esperado


def test_divisao_por_zero_falha_com_mensagem_clara_e_interrompe(sem_docker):
    f = soma(1, 0, "dividir")
    f["blocks"].append(bloco("depois", "builtin.matematica", {"operacao": "somar"}))
    f["blocks"].append(constante("k", "numero", 1))
    f["connections"] += [con("c4", "soma", "resultado", "depois", "a"), con("c5", "k", "valor", "depois", "b")]
    run = sem_docker.executar(f)
    assert run["state"] == "falhou"
    assert run["error"]["block_id"] == "soma" and "dividir por zero" in run["error"]["message"]
    e = etapas(run)
    assert e["soma"]["state"] == "falhou" and e["soma"]["error"]["code"] == "divisao_por_zero"
    assert e["soma"]["inputs"] == {"a": 1, "b": 0}  # diagnóstico preservado
    # dependentes e blocos que ainda não tinham rodado ficam ignorados, com o motivo
    assert e["depois"]["state"] == "ignorado" and "interrompido" in e["depois"]["skip_reason"]
    assert e["saida"]["state"] == "ignorado"
    assert run["result"]["outputs"] == []


def test_a_ordem_vem_das_dependencias_e_nao_da_posicao(sem_docker):
    f = soma(2, 3)
    f["blocks"].reverse()
    for i, b in enumerate(f["blocks"]):
        b["position"] = {"x": -1000 * i, "y": 777 - i * 500}
    run = sem_docker.executar(f)
    assert run["result"]["outputs"][0]["value"] == 5
    ordem = [e["block_id"] for e in sorted(run["steps"], key=lambda e: e["position"])]
    assert ordem.index("soma") > max(ordem.index("a"), ordem.index("b")) and ordem[-1] == "saida"


def test_uma_saida_alimenta_varios_blocos(sem_docker):
    f = fluxo(
        [constante("n", "numero", 5), bloco("dobro", "builtin.matematica", {"operacao": "multiplicar"}),
         bloco("mais1", "builtin.matematica", {"operacao": "somar"}), constante("dois", "numero", 2),
         constante("um", "numero", 1), saida("s1", "Dobro"), saida("s2", "Mais um")],
        [con("c1", "n", "valor", "dobro", "a"), con("c2", "dois", "valor", "dobro", "b"),
         con("c3", "n", "valor", "mais1", "a"), con("c4", "um", "valor", "mais1", "b"),
         con("c5", "dobro", "resultado", "s1", "valor"), con("c6", "mais1", "resultado", "s2", "valor")])
    run = sem_docker.executar(f)
    assert {o["title"]: o["value"] for o in run["result"]["outputs"]} == {"Dobro": 10, "Mais um": 6}


def condicao(valor):
    return fluxo(
        [bloco("inicio", "builtin.inicio", {"dados": {"idade": valor}}),
         bloco("campo", "builtin.selecionar_campos", {"caminhos": "idade"}),
         bloco("teste", "builtin.condicao", {"operador": "maior_igual", "comparar_com": "18"}, label="Maior de idade?"),
         bloco("posto", "builtin.matematica", {"operacao": "somar"}, label="Caminho verdadeiro"),
         constante("um", "numero", 1),
         bloco("depois", "builtin.matematica", {"operacao": "multiplicar"}, label="Depois do verdadeiro"),
         saida("sim", "Adulto"), saida("nao", "Menor")],
        [con("c1", "inicio", "dados", "campo", "objeto"), con("c2", "campo", "valor", "teste", "valor"),
         con("c3", "teste", "verdadeiro", "posto", "a"), con("c4", "um", "valor", "posto", "b"),
         con("c5", "posto", "resultado", "depois", "a"), con("c6", "um", "valor", "depois", "b"),
         con("c7", "depois", "resultado", "sim", "valor"), con("c8", "teste", "falso", "nao", "valor")])


def test_criterio_4_condicao_verdadeira_executa_so_o_caminho_verdadeiro(sem_docker):
    run = sem_docker.executar(condicao(20))
    e = etapas(run)
    assert run["state"] == "concluido"
    assert e["teste"]["outputs"] == {"resultado": True, "verdadeiro": 20}  # a outra porta nem é produzida
    assert [e[b]["state"] for b in ("posto", "depois", "sim")] == ["concluido"] * 3
    assert e["nao"]["state"] == "ignorado"
    assert "Se falso" in e["nao"]["skip_reason"] and "Maior de idade?" in e["nao"]["skip_reason"]
    assert e["nao"]["inputs"] is None and e["nao"]["outputs"] is None  # nunca executou
    assert run["result"]["outputs"] == [{"block_id": "sim", "title": "Adulto", "value": 21}]
    assert any("Se verdadeiro" in log["text"] for log in e["teste"]["logs"])


def test_criterio_4_condicao_falsa_executa_so_o_caminho_falso(sem_docker):
    run = sem_docker.executar(condicao(15))
    e = etapas(run)
    assert e["teste"]["outputs"] == {"resultado": False, "falso": 15}
    assert e["nao"]["state"] == "concluido"
    # tudo que depende do caminho verdadeiro é ignorado, inclusive o que vem depois (herança)
    assert [e[b]["state"] for b in ("posto", "depois", "sim")] == ["ignorado"] * 3
    assert "não foi escolhido" in e["depois"]["skip_reason"]
    assert run["result"]["outputs"] == [{"block_id": "nao", "title": "Menor", "value": 15}]
    assert e["um"]["state"] == "concluido"  # bloco comum (fora do caminho condicional) roda normalmente


@pytest.mark.parametrize("op,comparar,valor,esperado", [
    ("igual", "5", 5, True), ("igual", "5", "5", True), ("diferente", "5", 6, True), ("maior", "5", 6, True),
    ("menor", "5", 6, False), ("menor_igual", "6", 6, True), ("contem", "ol", "olá", True),
    ("nao_contem", "x", "olá", True), ("contem", "2", [1, 2, 3], True), ("vazio", "", "  ", True),
    ("nao_vazio", "", [1], True), ("verdadeiro", "", True, True), ("falso", "", True, False),
])
def test_operadores_da_condicao(sem_docker, op, comparar, valor, esperado):
    tipo = {bool: "booleano", int: "numero", str: "texto", list: "lista"}[type(valor)]
    f = fluxo([constante("v", tipo, valor), bloco("c", "builtin.condicao", {"operador": op, "comparar_com": comparar})],
              [con("c1", "v", "valor", "c", "valor")])
    assert sem_docker.executar(f)["steps"][1]["outputs"]["resultado"] is esperado


def test_comparacao_tipada_nao_confunde_texto_com_numero(sem_docker):
    def rodar(valor_tipo, valor, tipo_cmp):
        f = fluxo([constante("v", valor_tipo, valor),
                   bloco("c", "builtin.condicao", {"operador": "igual", "comparar_com": "5", "tipo_comparacao": tipo_cmp})],
                  [con("c1", "v", "valor", "c", "valor")])
        return sem_docker.executar(f)["steps"][1]["outputs"]["resultado"]

    assert rodar("texto", "5", "numero") is False   # o texto "5" não é o número 5
    assert rodar("numero", 5, "numero") is True
    assert rodar("numero", 5, "texto") is False
    assert rodar("booleano", True, "numero") is False  # True não é 1


def test_condicao_com_tipos_incomparaveis_da_erro_claro(sem_docker):
    f = fluxo([constante("v", "texto", "abc"), bloco("c", "builtin.condicao", {"operador": "maior", "comparar_com": "5", "tipo_comparacao": "numero"})],
              [con("c1", "v", "valor", "c", "valor")])
    run = sem_docker.executar(f)
    assert run["state"] == "falhou" and "Não é possível comparar" in run["error"]["message"]


def lista(itens, limite=100, operacao="multiplicar", **extra):
    params = {"operacao": operacao, "operando": 2, "limite": limite, **extra}
    return fluxo([constante("l", "lista", itens), bloco("cada", "builtin.para_cada", params), saida()],
                 [con("c1", "l", "valor", "cada", "lista"), con("c2", "cada", "resultado", "saida", "valor")])


def test_criterio_5_transforma_todos_os_itens_dentro_do_limite(sem_docker):
    run = sem_docker.executar(lista(list(range(1, 51)), limite=50))
    saida_ = run["result"]["outputs"][0]["value"]
    assert saida_ == [n * 2 for n in range(1, 51)]
    assert etapas(run)["cada"]["outputs"]["quantidade"] == 50


def test_criterio_5_acima_do_limite_falha_sem_cortar_a_lista_em_silencio(sem_docker):
    run = sem_docker.executar(lista(list(range(11)), limite=10))
    assert run["state"] == "falhou"
    e = etapas(run)["cada"]
    assert e["error"]["code"] == "limite_itens" and "11 itens" in e["error"]["message"] and "10" in e["error"]["message"]
    assert run["result"]["outputs"] == []


@pytest.mark.parametrize("op,extra,entrada,esperado", [
    ("maiusculas", {}, ["a", "bc"], ["A", "BC"]),
    ("adicionar_texto", {"prefixo": "<", "sufixo": ">"}, ["a", 2], ["<a>", "<2>"]),
    ("para_numero", {}, ["1", "2,5", 3], [1, 2.5, 3]),
    ("extrair_campo", {"campo": "a.b"}, [{"a": {"b": 1}}, {"a": {"b": 2}}], [1, 2]),
    ("somar", {"operando": 10}, [1, 2.5], [11, 12.5]),
])
def test_transformacoes_da_lista(sem_docker, op, extra, entrada, esperado):
    run = sem_docker.executar(lista(entrada, operacao=op, **extra))
    assert run["result"]["outputs"][0]["value"] == esperado


def test_item_invalido_indica_a_posicao(sem_docker):
    run = sem_docker.executar(lista([1, "dois", 3]))
    e = etapas(run)["cada"]["error"]
    assert run["state"] == "falhou" and "item 2" in e["message"] and e["technical"]["item_index"] == 1


def test_selecionar_campos_caminhos_e_erro_de_campo_ausente(sem_docker):
    dados = {"usuario": {"nome": "Ana", "tags": ["a", "b"]}, "idade": 30}
    f = fluxo([bloco("i", "builtin.inicio", {"dados": dados}),
               bloco("s", "builtin.selecionar_campos", {"caminhos": "usuario.nome\nusuario.tags.1\nidade"}), saida()],
              [con("c1", "i", "dados", "s", "objeto"), con("c2", "s", "selecionados", "saida", "valor")])
    run = sem_docker.executar(f)
    assert run["result"]["outputs"][0]["value"] == {"usuario.nome": "Ana", "usuario.tags.1": "b", "idade": 30}
    f["blocks"][1]["params"] = {"caminhos": "email"}
    run = sem_docker.executar(f)
    assert run["state"] == "falhou" and "“email” não existe" in run["error"]["message"]
    assert "usuario, idade" in run["error"]["suggestion"]
    f["blocks"][1]["params"] = {"caminhos": "email", "se_ausente": "nulo"}
    assert sem_docker.executar(f)["result"]["outputs"][0]["value"] == {"email": None}


def test_transformar_texto(sem_docker):
    def rodar(op, texto="  Olá mundo ", **params):
        f = fluxo([constante("t", "texto", texto), bloco("x", "builtin.texto", {"operacao": op, **params}), saida()],
                  [con("c1", "t", "valor", "x", "texto"), con("c2", "x", "resultado", "saida", "valor")])
        return sem_docker.executar(f)["result"]["outputs"][0]["value"]

    assert rodar("maiusculas") == "  OLÁ MUNDO "
    assert rodar("remover_espacos") == "Olá mundo"
    assert rodar("titulo", "ana maria") == "Ana Maria"
    assert rodar("inverter", "abc") == "cba"
    assert rodar("substituir", "a-b-c", buscar="-", substituir_por="+") == "a+b+c"
    assert rodar("prefixo_sufixo", "x", prefixo="[", sufixo="]") == "[x]"


def test_registro_da_execucao_tem_ids_estados_horarios_e_duracao(sem_docker):
    run = sem_docker.executar(soma())
    assert run["id"].startswith("exe_") and run["kind"] == "fluxo"
    assert run["created_at"] <= run["started_at"] <= run["finished_at"]
    assert isinstance(run["duration_ms"], int) and run["duration_ms"] >= 0
    for e in run["steps"]:
        assert e["state"] == "concluido" and e["started_at"] and e["finished_at"] and e["duration_ms"] is not None
        assert e["logs"] == [] and e["error"] is None
    soma_ = etapas(run)["soma"]
    assert soma_["inputs"] == {"a": 2, "b": 3} and soma_["outputs"] == {"resultado": 5}
    # o snapshot guarda o fluxo com as versões fixadas
    completo = sem_docker.store.obter_execucao(run["id"], com_snapshot=True)["snapshot"]
    assert {b["version"] for b in completo["flow"]["blocks"]} == {1} and "builtin.matematica@1" in completo["definitions"]


def test_dados_iniciais_da_execucao_substituem_os_do_bloco_inicio(sem_docker):
    f = fluxo([bloco("i", "builtin.inicio", {"dados": {"x": 1}}), saida()], [con("c1", "i", "dados", "saida", "valor")])
    assert sem_docker.executar(f)["result"]["outputs"][0]["value"] == {"x": 1}
    assert sem_docker.executar(f, {"i": {"x": 99}})["result"]["outputs"][0]["value"] == {"x": 99}
    with pytest.raises(ApiError) as exc:
        sem_docker.executar(f, {"inexistente": {"x": 1}})
    assert exc.value.codigo == "dados_iniciais_invalidos"


@pytest.mark.parametrize("construir,codigo", [
    (lambda: fluxo([bloco("a", "builtin.matematica"), bloco("b", "builtin.matematica")],
                   [con("c1", "a", "resultado", "b", "a"), con("c2", "b", "resultado", "a", "a")]), "ciclo"),
    (lambda: fluxo([constante("t", "texto", "x"), bloco("m", "builtin.matematica")], [con("c1", "t", "valor", "m", "a")]),
     "tipo_incompativel"),
    (lambda: fluxo([bloco("m", "builtin.matematica")]), "entrada_obrigatoria"),
    (lambda: fluxo([bloco("s", "builtin.selecionar_campos", {})]), "parametro_invalido"),
    (lambda: fluxo([]), "fluxo_vazio"),
])
def test_criterio_6_fluxo_invalido_e_rejeitado_antes_de_executar(sem_docker, construir, codigo):
    with pytest.raises(ApiError) as exc:
        sem_docker.executar(construir())
    assert exc.value.status == 422 and exc.value.codigo == "fluxo_invalido"
    assert codigo in [p["code"] for p in exc.value.problemas]
    # nada foi registrado: a execução nem foi criada
    assert sem_docker.store.listar_execucoes("qualquer") == []
    with sem_docker.store._conexao() as c:
        assert c.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_valor_incompativel_em_tempo_de_execucao_e_barrado_pelo_tipo_da_entrada(sem_docker):
    # selecionar_campos.valor é "qualquer"; aqui o valor real é um texto e a soma espera número
    f = fluxo([bloco("i", "builtin.inicio", {"dados": {"x": "abc"}}),
               bloco("s", "builtin.selecionar_campos", {"caminhos": "x"}), constante("n", "numero", 1),
               bloco("m", "builtin.matematica"), saida()],
              [con("c1", "i", "dados", "s", "objeto"), con("c2", "s", "valor", "m", "a"), con("c3", "n", "valor", "m", "b"),
               con("c4", "m", "resultado", "saida", "valor")])
    run = sem_docker.executar(f)
    assert run["state"] == "falhou"
    e = etapas(run)["m"]["error"]
    assert e["code"] == "entrada_invalida" and "esperava número" in e["message"] and "recebeu texto" in e["message"]


def test_sem_executor_o_fluxo_com_python_e_recusado_e_o_resto_continua_funcionando(sem_docker):
    com_python = fluxo([constante("l", "lista", [1]),
                        bloco("p", "builtin.para_cada", {"operacao": "python", "limite": 5}), saida()],
                       [con("c1", "l", "valor", "p", "lista"), con("c2", "p", "resultado", "saida", "valor")])
    with pytest.raises(ApiError) as exc:
        sem_docker.executar(com_python)
    problema = next(p for p in exc.value.problemas if p["code"] == "executor_indisponivel")
    assert problema["block_id"] == "p" and "Docker" in problema["message"]
    # fluxos sem código personalizado seguem normalmente
    assert sem_docker.executar(soma())["state"] == "concluido"


def test_teste_isolado_de_bloco_registra_execucao(sem_docker):
    tipo = sem_docker.registro.resolver("builtin.matematica", 1)
    run = sem_docker.motor.testar_bloco(tipo, {"operacao": "multiplicar"}, {"a": 6, "b": 7})
    assert run["kind"] == "bloco" and run["state"] == "concluido"
    assert run["steps"][0]["outputs"] == {"resultado": 42}
    with pytest.raises(ApiError) as exc:
        sem_docker.motor.testar_bloco(tipo, {"operacao": "somar"}, {"a": 1})
    assert "Informe um valor de exemplo" in exc.value.problemas[0]["message"]
    with pytest.raises(ApiError):
        sem_docker.motor.testar_bloco(tipo, {"operacao": "somar"}, {"a": "x", "b": 1})
