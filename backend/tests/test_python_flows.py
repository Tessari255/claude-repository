"""Fluxos com código Python executado de verdade no contêiner isolado: o passo "Executar código Python" (inline) e
os blocos Python reutilizáveis da biblioteca. Contrato do código, erros com linha, limite de tempo, versões fixadas."""

from __future__ import annotations

import dataclasses

import pytest
from pydantic import ValidationError

from app import custom_blocks
from app.config import Limites
from app.errors import ApiError
from app.exchange import importar
from app.models import BlockDraft, Flow
from app.sandbox import DockerExecutor
from app.validation import analisar

from .conftest import IMAGEM, _montar
from .helpers import (campo, carregar_exemplo, compor, estados, etapas, fluxo, lit, passo, python_inline, ref, repeticoes, saida)

pytestmark = pytest.mark.docker

CODIGO_DO_ENUNCIADO = '''def run(inputs: dict, params: dict) -> dict:
    nome = inputs.get("nome", "mundo")
    return {"mensagem": f"Olá, {nome}!"}
'''


def valores(run):
    return {o["title"]: o["value"] for o in run["result"]["outputs"]}


# ------------------------------------------------------------------ Python inline
def inline(codigo, entradas=None, saidas=None, **kw):
    return python_inline("p", codigo, entradas, saidas if saidas is not None else {"mensagem": "texto"}, **kw)


def com_saida(passos, titulo="mensagem", origem=("p", "mensagem"), campos=None):
    return fluxo([*passos, saida("s", titulo, ref(*origem))], campos)


def test_gatilho_python_saida_com_o_codigo_do_enunciado(com_docker):
    f = com_saida([inline(CODIGO_DO_ENUNCIADO, {"nome": ("texto", ref("gatilho", "nome"))})], campos=[campo("nome", "texto", "Ana")])
    run = com_docker.executar(f)
    assert run["state"] == "concluido", run["error"]
    assert valores(run) == {"mensagem": "Olá, Ana!"}
    e = etapas(run)["p"]
    assert e["inputs"] == {"nome": "Ana"} and e["outputs"] == {"mensagem": "Olá, Ana!"}


def test_entrada_opcional_nao_preenchida_nao_aparece_em_inputs(com_docker):
    p = inline(CODIGO_DO_ENUNCIADO)
    p["params"]["entradas"] = [{"id": "nome", "label": "Nome", "type": "texto", "required": False}]
    run = com_docker.executar(com_saida([p]))
    assert valores(run) == {"mensagem": "Olá, mundo!"} and etapas(run)["p"]["inputs"] == {}


def test_entrada_com_texto_e_conteudo_dinamico(com_docker):
    from .helpers import tpl
    f = com_saida([inline("def run(inputs, params):\n    return {'mensagem': inputs['nome'].upper()}",
                          {"nome": ("texto", tpl("sr. ", ("gatilho", "nome")))})], campos=[campo("nome", "texto", "ana")])
    assert valores(com_docker.executar(f)) == {"mensagem": "SR. ANA"}


def test_excecao_identifica_o_passo_o_erro_e_a_linha(com_docker):
    codigo = 'def run(inputs, params):\n    lista = [1, 2, 3]\n    return {"mensagem": lista[10]}\n'
    run = com_docker.executar(com_saida([inline(codigo, label="Pegar o décimo item")]))
    assert run["state"] == "falhou"
    erro = run["error"]
    assert erro["step_id"] == "p" and erro["step_name"] == "Pegar o décimo item"
    assert erro["code"] == "excecao_python" and erro["line"] == 3
    assert "linha 3" in erro["message"] and "posição que não existe" in erro["message"]
    t = erro["technical"]
    assert t["type"] == "IndexError" and t["snippet"] == 'return {"mensagem": lista[10]}' and "list index out of range" in t["message"]
    assert 'File "<bloco>", line 3' in t["traceback"]
    assert estados(run)["p"] == "falhou" and estados(run)["s"] == "ignorado"


def test_print_vira_log_do_passo_e_segredos_sao_mascarados(com_docker):
    codigo = 'def run(inputs, params):\n    print("olá")\n    print("senha=abc123")\n    import sys\n    return {"mensagem": "ok"}\n'
    run = com_docker.executar(com_saida([inline(codigo)]))
    texto = "".join(p["text"] for p in etapas(run)["p"]["logs"])
    assert run["state"] == "falhou" and "não está disponível" in run["error"]["message"]  # `sys` está fora da lista
    assert "olá" in texto and "abc123" not in texto and "[oculto]" in texto


def test_laco_infinito_e_encerrado_pelo_limite_de_tempo_e_conta_como_expirou(settings):
    rapido = DockerExecutor(IMAGEM, Limites(tempo_s=1.5, memoria_mb=128, folga_inicio_s=4.0))
    amb = _montar(dataclasses.replace(settings, limites=rapido.limites), rapido)
    f = fluxo([inline("def run(inputs, params):\n    n = 0\n    while True:\n        n += 1\n"),
               compor("trata_falha", lit("falha comum"), run_after=["falhou"]),
               compor("trata_tempo", lit("expirou"), run_after=["expirou"])])
    run = amb.executar(f)
    e = etapas(run)
    assert e["p"]["error"]["code"] == "tempo_esgotado" and "1.5 s" in e["p"]["error"]["message"]
    assert e["p"]["error"]["technical"]["line"] in (3, 4) and "laços sem fim" in e["p"]["error"]["suggestion"]
    assert e["p"]["duration_ms"] < 8000
    # "expirou" e "falhou" são situações diferentes: só o passo configurado para expirar roda
    assert e["trata_falha"]["state"] == "ignorado"
    f2 = fluxo([inline("def run(inputs, params):\n    while True:\n        pass\n"), compor("trata_tempo", lit("expirou"), run_after=["expirou"])])
    run2 = amb.executar(f2)
    assert estados(run2)["trata_tempo"] == "concluido" and run2["state"] == "concluido"


def test_tempo_limite_do_proprio_passo_e_menor_que_o_do_servidor(com_docker):
    f = fluxo([inline("def run(inputs, params):\n    while True:\n        pass\n", timeout=1)])
    run = com_docker.executar(f)
    assert run["state"] == "falhou" and "1 s" in run["error"]["message"]


def test_tentativas_repetem_o_codigo_python_que_falhou(com_docker):
    run = com_docker.executar(com_saida([inline("def run(inputs, params):\n    return {'mensagem': 1/0}", retry=1)]))
    logs = [l["text"] for l in etapas(run)["p"]["logs"]]
    assert run["state"] == "falhou" and any("Tentativa 1 de 2 falhou" in t for t in logs)


@pytest.mark.parametrize("codigo,codigo_do_erro,trecho", [
    ("def run(inputs, params):\n    return {}\n", "retorno_invalido", "não devolveu a saída declarada"),
    ('def run(inputs, params):\n    return {"mensagem": "x", "extra": 1}\n', "retorno_invalido", "extra"),
    ('def run(inputs, params):\n    return {"mensagem": 42}\n', "retorno_invalido", "deveria ser texto"),
    ('def run(inputs, params):\n    return {"mensagem": {1, 2}}\n', "retorno_invalido", "JSON"),
    ('def run(inputs, params):\n    return "texto solto"\n', "retorno_invalido", ""),
])
def test_contrato_do_retorno(com_docker, codigo, codigo_do_erro, trecho):
    e = com_docker.executar(com_saida([inline(codigo)]))["error"]
    assert e["code"] == codigo_do_erro and trecho in e["message"]


def test_dados_json_e_listas_passam_intactos_e_unicode_tambem(com_docker):
    codigo = 'def run(inputs, params):\n    d = inputs["dados"]\n    return {"total": sum(d["valores"]), "copia": d}\n'
    dados = {"valores": [1, 2, 3.5], "nome": "ç"}
    p = python_inline("p", codigo, {"dados": ("json", ref("gatilho", "d"))}, {"total": "numero", "copia": "json"})
    f = fluxo([p, saida("s1", "total", ref("p", "total")), saida("s2", "copia", ref("p", "copia"))], [campo("d", "json", dados)])
    assert valores(com_docker.executar(f)) == {"total": 6.5, "copia": dados}


def test_codigo_dentro_de_um_laco_roda_uma_vez_por_item(com_docker):
    p = python_inline("quadrado", "def run(inputs, params):\n    return {'n': inputs['n'] ** 2}", {"n": ("numero", ref("laco", "item"))}, {"n": "numero"})
    f = fluxo([passo("laco", "builtin.para_cada", {"lista": ref("gatilho", "l")}, {"limite": 5}, slots={"corpo": [p]})], [campo("l", "lista", [2, 3, 4])])
    run = com_docker.executar(f)
    assert [repeticoes(run, "quadrado")[(i,)]["outputs"]["n"] for i in range(3)] == [4, 9, 16]


def test_passos_python_encadeados_rodam_em_conteineres_separados_sem_dividir_arquivos(com_docker):
    a = python_inline("a", 'def run(inputs, params):\n    open("/tmp/segredo", "w").write("x")\n    return {"mensagem": "a"}\n', saidas={"mensagem": "texto"})
    b = python_inline("b", 'def run(inputs, params):\n    try:\n        open("/tmp/segredo")\n        return {"mensagem": "vazou"}\n'
                           '    except OSError:\n        return {"mensagem": "isolado"}\n', saidas={"mensagem": "texto"})
    assert valores(com_docker.executar(fluxo([a, b, saida("s", "r", ref("b", "mensagem"))]))) == {"r": "isolado"}


def test_transformar_lista_com_python_roda_tudo_em_uma_so_execucao(com_docker):
    codigo = "def transformar(item, indice):\n    return {'n': item, 'quadrado': item * item, 'pos': indice}\n"
    f = fluxo([passo("t", "builtin.transformar_lista", {"lista": lit([1, 2, 3, 4])}, {"operacao": "python", "codigo": codigo, "limite": 10}),
               saida("s", "r", ref("t", "resultado"))])
    assert valores(com_docker.executar(f))["r"] == [{"n": n, "quadrado": n * n, "pos": n - 1} for n in (1, 2, 3, 4)]
    f["steps"][0]["params"]["codigo"] = "def transformar(item):\n    return 10 // item\n"
    f["steps"][0]["inputs"]["lista"] = lit([5, 0, 1])
    erro = com_docker.executar(f)["error"]
    assert erro["code"] == "excecao_python" and "No item 2" in erro["message"] and erro["line"] == 2


# ------------------------------------------------------------------ blocos Python reutilizáveis
def declarar(codigo, nome="Meu bloco", entradas=None, saidas=None, params=None) -> BlockDraft:
    return BlockDraft.model_validate({
        "name": nome, "code": codigo,
        "inputs": entradas if entradas is not None else [{"id": "nome", "label": "Nome", "type": "texto", "required": False}],
        "outputs": saidas if saidas is not None else [{"id": "mensagem", "label": "Mensagem", "type": "texto"}],
        "params": params or []})


def criar(amb, codigo, **kw):
    return custom_blocks.criar_bloco(amb.store, amb.executor, declarar(codigo, **kw))["block"]


def usando(b, entradas=None, params=None, campos=None):
    p = passo("p", b["id"], entradas or {}, params or {}, versao=b["version"])
    return fluxo([p, saida("s", "mensagem", ref("p", "mensagem"))], campos)


def test_bloco_da_biblioteca_com_entrada_opcional_e_parametros_com_padrao(com_docker):
    b = criar(com_docker, 'def run(inputs, params):\n    return {"mensagem": params["prefixo"] + inputs.get("nome", "?")}',
              params=[{"id": "prefixo", "label": "Prefixo", "type": "texto", "required": True, "default": "Oi, "}])
    assert valores(com_docker.executar(usando(b))) == {"mensagem": "Oi, ?"}
    run = com_docker.executar(usando(b, {"nome": ref("gatilho", "n")}, {"prefixo": "Tchau, "}, [campo("n", "texto", "Lia")]))
    assert valores(run) == {"mensagem": "Tchau, Lia"}
    with pytest.raises(ApiError) as exc:  # obrigatório e vazio → rejeitado antes de executar
        com_docker.executar(usando(b, params={"prefixo": "  "}))
    assert any(p["field"] == "prefixo" for p in exc.value.problemas)


def test_bloco_salvo_e_reutilizado_em_outro_fluxo(com_docker):
    b = criar(com_docker, 'def run(inputs, params):\n    return {"mensagem": inputs["nome"].upper()}',
              nome="Caixa alta", entradas=[{"id": "nome", "label": "Nome", "type": "texto"}])
    assert b["id"] in {t.id for t in com_docker.registro.listar()}
    for nome in ("ana", "beto"):
        run = com_docker.executar(usando(b, {"nome": ref("gatilho", "n")}, campos=[campo("n", "texto", nome)]))
        assert valores(run) == {"mensagem": nome.upper()}


def test_versao_fixada_edicao_posterior_nao_altera_fluxos_existentes(com_docker):
    v1 = criar(com_docker, 'def run(inputs, params):\n    return {"mensagem": "versão 1"}\n', entradas=[])
    f_antigo = usando(v1)
    v2 = custom_blocks.nova_versao(com_docker.store, com_docker.registro, com_docker.executor, v1["id"],
                                   declarar('def run(inputs, params):\n    return {"mensagem": "versão 2"}\n', entradas=[]))["block"]
    assert v2["version"] == 2 and v2["id"] == v1["id"]
    assert valores(com_docker.executar(f_antigo)) == {"mensagem": "versão 1"}  # o fluxo antigo continua igual
    assert valores(com_docker.executar(usando(v2))) == {"mensagem": "versão 2"}
    assert {(t.id, t.version) for t in com_docker.registro.listar(todas_versoes=True) if t.id == v1["id"]} == {(v1["id"], 1), (v1["id"], 2)}
    a = analisar(Flow.model_validate(f_antigo), com_docker.registro.resolver, ultima_versao=com_docker.registro.ultima_versao)
    aviso = next(i for i in a.issues if i.code == "versao_desatualizada")
    assert aviso.severity == "aviso" and "v2" in aviso.message and "v1" in aviso.message


def test_bloco_em_uso_dentro_de_um_ramo_nao_pode_ser_excluido(com_docker):
    b = criar(com_docker, 'def run(inputs, params):\n    return {"mensagem": "x"}\n', entradas=[])
    aninhado = fluxo([passo("e", "builtin.escopo", slots={"corpo": [passo("p", b["id"], versao=1)]})])
    com_docker.store.criar_projeto("Usa o bloco", "", aninhado)
    with pytest.raises(ApiError) as exc:
        custom_blocks.excluir_bloco(com_docker.store, b["id"])
    assert exc.value.status == 409 and "Usa o bloco" in exc.value.mensagem


def test_salvar_bloco_com_erro_de_sintaxe_ou_sem_run_e_recusado_com_a_linha(com_docker):
    with pytest.raises(ApiError) as exc:
        custom_blocks.criar_bloco(com_docker.store, com_docker.executor, declarar("def run(inputs, params)\n    return {}"))
    assert exc.value.codigo == "codigo_invalido" and "linha 1" in exc.value.mensagem
    with pytest.raises(ApiError) as exc:
        custom_blocks.criar_bloco(com_docker.store, com_docker.executor, declarar("x = 1"))
    assert "def run(inputs, params)" in exc.value.mensagem
    assert com_docker.store.listar_tipos() == []


def test_declaracao_do_bloco_e_validada(com_docker):
    with pytest.raises(ValidationError):
        declarar("def run(i, p):\n    return {}", saidas=[{"id": "Mensagem Ruim", "label": "x", "type": "texto"}])
    with pytest.raises(ValidationError):
        declarar("def run(i, p):\n    return {}", entradas=[{"id": "a", "label": "A"}, {"id": "a", "label": "B"}])
    with pytest.raises(ApiError):
        custom_blocks.criar_bloco(com_docker.store, com_docker.executor, declarar("def run(i, p):\n    return {}", saidas=[]))


def test_teste_isolado_de_bloco_python_com_dados_de_exemplo(com_docker):
    draft = declarar('def run(inputs, params):\n    print("testando")\n    return {"mensagem": params["prefixo"] + inputs["nome"]}',
                     entradas=[{"id": "nome", "label": "Nome", "type": "texto"}],
                     params=[{"id": "prefixo", "label": "Prefixo", "type": "texto", "default": "Oi, "}])
    tipo = draft.para_tipo("custom.rascunho", 1)
    run = com_docker.motor.testar_bloco(tipo, {}, {"nome": "Lia"})
    assert run["kind"] == "bloco" and run["state"] == "concluido"
    s = run["steps"][0]
    assert s["outputs"] == {"mensagem": "Oi, Lia"} and s["logs"] == [{"source": "stdout", "text": "testando\n"}]
    quebrado = draft.model_copy(update={"code": 'def run(inputs, params):\n    return {"mensagem": 1/0}'}).para_tipo("custom.rascunho", 1)
    erro = com_docker.motor.testar_bloco(quebrado, {}, {"nome": "x"})
    assert erro["state"] == "falhou" and erro["error"]["line"] == 2 and erro["error"]["step_id"] == "teste"


# ------------------------------------------------------------------ os modelos entregues
def test_modelos_com_python_produzem_os_resultados_documentados(com_docker):
    def rodar(arquivo, dados=None):
        res = importar(com_docker.store, com_docker.registro, carregar_exemplo(arquivo))
        return com_docker.executar(res["flow"], dados)

    assert valores(rodar("01-saudacao.json")) == {"Saudação": "Olá, Ana!"}
    assert valores(rodar("01-saudacao.json", {"nome": "  beto silva "})) == {"Saudação": "Olá, Beto Silva!"}
    erro = rodar("05-tratar-erros.json")
    assert erro["state"] == "concluido"  # a falha foi capturada
    assert valores(erro)["Aviso"].startswith("Não foi possível calcular: O código tentou dividir por zero")
    assert estados(erro)["tentar"] == "falhou" and estados(erro)["capturar"] == "concluido"
    sem_erro = rodar("05-tratar-erros.json", {"divisor": 4})  # sem falha, o passo de captura é ignorado e o resultado aparece
    assert valores(sem_erro) == {"Resultado da divisão": 25} and estados(sem_erro)["capturar"] == "ignorado"
    laco = rodar("06-laco-e-variavel.json")
    assert valores(laco) == {"Soma": 60, "Média": 20}
    assert sorted(repeticoes(laco, "somar")) == [(0,), (1,), (2,)]
