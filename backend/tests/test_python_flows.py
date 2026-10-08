"""Fluxos com código Python PERSONALIZADO, executado de verdade no contêiner isolado.
Critérios 2, 7, 8 e 9, contrato do bloco e versões fixadas."""

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

from .conftest import IMAGEM, _montar
from .helpers import bloco, carregar_exemplo, con, constante, etapas, fluxo, saida

pytestmark = pytest.mark.docker

CODIGO_DO_ENUNCIADO = '''def run(inputs: dict, params: dict) -> dict:
    nome = inputs.get("nome", "mundo")
    return {"mensagem": f"Olá, {nome}!"}
'''


def declarar(codigo, nome="Meu bloco", entradas=None, saidas=None, params=None) -> BlockDraft:
    return BlockDraft.model_validate({
        "name": nome, "code": codigo,
        "inputs": entradas if entradas is not None else [{"id": "nome", "label": "Nome", "type": "texto", "required": False}],
        "outputs": saidas if saidas is not None else [{"id": "mensagem", "label": "Mensagem", "type": "texto"}],
        "params": params or []})


def criar(amb, codigo, **kw):
    res = custom_blocks.criar_bloco(amb.store, amb.executor, declarar(codigo, **kw))
    return res["block"]


def fluxo_com(bloco_def, entradas=None, params=None, extra_saidas=("mensagem",)):
    """início → (campo) → bloco personalizado → saída(s)."""
    blocos = [bloco("inicio", "builtin.inicio", {"dados": entradas or {}}),
              bloco("p", bloco_def["id"], params or {}, versao=bloco_def["version"])]
    conexoes = []
    if entradas:
        blocos.append(bloco("campo", "builtin.selecionar_campos", {"caminhos": next(iter(entradas))}))
        conexoes += [con("c1", "inicio", "dados", "campo", "objeto"), con("c2", "campo", "valor", "p", "nome")]
    for i, porta in enumerate(extra_saidas):
        blocos.append(saida(f"s{i}", porta))
        conexoes.append(con(f"o{i}", "p", porta, f"s{i}", "valor"))
    return fluxo(blocos, conexoes)


def test_criterio_2_inicio_com_nome_funcao_python_de_saudacao_saida(com_docker):
    res = importar(com_docker.store, com_docker.registro, carregar_exemplo("01-saudacao.json"))
    run = com_docker.executar(res["flow"])
    assert run["state"] == "concluido", run["error"]
    assert run["result"]["outputs"] == [{"block_id": "saida", "title": "Mensagem", "value": "Olá, Ana!"}]
    e = etapas(run)
    assert e["campo"]["outputs"]["valor"] == "Ana"
    assert e["saudar"]["inputs"] == {"nome": "Ana"} and e["saudar"]["outputs"] == {"mensagem": "Olá, Ana!"}


def test_codigo_exatamente_como_no_enunciado_e_entrada_opcional_ausente(com_docker):
    b = criar(com_docker, CODIGO_DO_ENUNCIADO)
    run = com_docker.executar(fluxo_com(b))  # "nome" sem ligação → inputs.get usa o padrão
    assert run["result"]["outputs"][0]["value"] == "Olá, mundo!"
    assert etapas(run)["p"]["inputs"] == {}
    run = com_docker.executar(fluxo_com(b, {"nome": "Beto"}))
    assert run["result"]["outputs"][0]["value"] == "Olá, Beto!"


def test_parametros_do_bloco_chegam_em_params_com_padrao(com_docker):
    b = criar(com_docker, 'def run(inputs, params):\n    return {"mensagem": params["prefixo"] + "!"}',
              entradas=[], params=[{"id": "prefixo", "label": "Prefixo", "type": "texto", "required": True, "default": "Oi"}])
    assert com_docker.executar(fluxo_com(b))["result"]["outputs"][0]["value"] == "Oi!"
    assert com_docker.executar(fluxo_com(b, params={"prefixo": "Tchau"}))["result"]["outputs"][0]["value"] == "Tchau!"
    with pytest.raises(ApiError) as exc:  # obrigatório e vazio → rejeitado antes de executar
        com_docker.executar(fluxo_com(b, params={"prefixo": "  "}))
    assert any(p["param"] == "prefixo" for p in exc.value.problemas)


def test_criterio_7_excecao_identifica_o_bloco_o_erro_e_a_linha(com_docker):
    b = criar(com_docker, 'def run(inputs, params):\n    lista = [1, 2, 3]\n    return {"mensagem": lista[10]}\n',
              nome="Pega item", entradas=[])
    f = fluxo_com(b)
    f["blocks"][1]["label"] = "Pegar o décimo item"
    run = com_docker.executar(f)
    assert run["state"] == "falhou"
    erro = run["error"]
    assert erro["block_id"] == "p" and erro["block_name"] == "Pegar o décimo item"
    assert erro["code"] == "excecao_python" and erro["line"] == 3
    assert "linha 3" in erro["message"] and "posição que não existe" in erro["message"]
    tecnico = erro["technical"]
    assert tecnico["type"] == "IndexError" and tecnico["line"] == 3
    assert tecnico["snippet"] == 'return {"mensagem": lista[10]}' and "list index out of range" in tecnico["message"]
    assert 'File "<bloco>", line 3' in tecnico["traceback"]
    e = etapas(run)
    assert e["p"]["state"] == "falhou" and e["s0"]["state"] == "ignorado"  # o fluxo parou nesse bloco


def test_stdout_e_stderr_viram_logs_do_bloco_e_segredos_sao_mascarados(com_docker):
    codigo = ('def run(inputs, params):\n    print("olá")\n    print("senha=abc123")\n'
              '    import sys\n    return {"mensagem": "ok"}\n')
    # `sys` está fora da lista: o erro mostra isso, mas os logs anteriores são preservados
    b = criar(com_docker, codigo, entradas=[])
    run = com_docker.executar(fluxo_com(b))
    e = etapas(run)["p"]
    assert run["state"] == "falhou" and "não está disponível" in run["error"]["message"]
    texto = "".join(p["text"] for p in e["logs"])
    assert "olá" in texto and "abc123" not in texto and "[oculto]" in texto


def test_criterio_8_laco_infinito_e_encerrado_pelo_limite_de_tempo(settings, executor):
    rapido = DockerExecutor(IMAGEM, Limites(tempo_s=1.5, memoria_mb=128, folga_inicio_s=4.0))
    amb = _montar(dataclasses.replace(settings, limites=rapido.limites), rapido)
    b = criar(amb, 'def run(inputs, params):\n    n = 0\n    while True:\n        n += 1\n', entradas=[])
    run = amb.executar(fluxo_com(b))
    assert run["state"] == "falhou"
    assert run["error"]["code"] == "tempo_esgotado" and run["error"]["block_id"] == "p"
    assert "1.5 s" in run["error"]["message"]
    assert run["error"]["line"] in (3, 4)  # onde o laço estava quando o limite estourou
    assert "laços sem fim" in run["error"]["suggestion"]
    assert etapas(run)["p"]["duration_ms"] < 8000


def test_contrato_retorno_com_saida_declarada_faltando(com_docker):
    b = criar(com_docker, 'def run(inputs, params):\n    return {}\n', entradas=[])
    run = com_docker.executar(fluxo_com(b))
    e = run["error"]
    assert e["code"] == "retorno_invalido" and "não devolveu a saída declarada" in e["message"] and "mensagem" in e["message"]


def test_contrato_retorno_com_saida_nao_declarada(com_docker):
    b = criar(com_docker, 'def run(inputs, params):\n    return {"mensagem": "x", "extra": 1}\n', entradas=[])
    e = com_docker.executar(fluxo_com(b))["error"]
    assert e["code"] == "retorno_invalido" and "extra" in e["message"]


def test_contrato_retorno_com_tipo_errado(com_docker):
    b = criar(com_docker, 'def run(inputs, params):\n    return {"mensagem": 42}\n', entradas=[])
    e = com_docker.executar(fluxo_com(b))["error"]
    assert e["code"] == "retorno_invalido" and "deveria ser texto" in e["message"] and "número" in e["message"]


def test_contrato_retorno_nao_serializavel_e_nao_dict(com_docker):
    b = criar(com_docker, 'def run(inputs, params):\n    return {"mensagem": {1, 2}}\n', entradas=[])
    e = com_docker.executar(fluxo_com(b))["error"]
    assert e["code"] == "retorno_invalido" and "JSON" in e["message"]
    b2 = criar(com_docker, 'def run(inputs, params):\n    return "texto solto"\n', entradas=[])
    assert com_docker.executar(fluxo_com(b2))["error"]["code"] == "retorno_invalido"


def test_dados_entre_blocos_sao_json_e_listas_e_objetos_passam_intactos(com_docker):
    b = criar(com_docker,
              'def run(inputs, params):\n    d = inputs["dados"]\n    return {"total": sum(d["valores"]), "copia": d}\n',
              entradas=[{"id": "dados", "label": "Dados", "type": "json"}],
              saidas=[{"id": "total", "label": "Total", "type": "numero"}, {"id": "copia", "label": "Cópia", "type": "json"}])
    f = fluxo([bloco("i", "builtin.inicio", {"dados": {"valores": [1, 2, 3.5], "nome": "ç"}}), bloco("p", b["id"], versao=1),
               saida("s1", "total"), saida("s2", "copia")],
              [con("c1", "i", "dados", "p", "dados"), con("c2", "p", "total", "s1", "valor"), con("c3", "p", "copia", "s2", "valor")])
    run = com_docker.executar(f)
    valores = {o["title"]: o["value"] for o in run["result"]["outputs"]}
    assert valores == {"total": 6.5, "copia": {"valores": [1, 2, 3.5], "nome": "ç"}}


def test_criterio_9_bloco_salvo_e_reutilizado_em_outro_fluxo(com_docker):
    b = criar(com_docker, 'def run(inputs, params):\n    return {"mensagem": inputs["nome"].upper()}\n',
              nome="Caixa alta", entradas=[{"id": "nome", "label": "Nome", "type": "texto"}])
    biblioteca = {t.id for t in com_docker.registro.listar()}
    assert b["id"] in biblioteca  # aparece na biblioteca
    for projeto, nome in (("um", "ana"), ("dois", "beto")):
        run = com_docker.executar(fluxo_com(b, {"nome": nome}))
        assert run["result"]["outputs"][0]["value"] == nome.upper(), projeto


def test_versao_fixada_edicao_posterior_nao_altera_fluxos_existentes(com_docker):
    v1 = criar(com_docker, 'def run(inputs, params):\n    return {"mensagem": "versão 1"}\n', entradas=[])
    f_antigo = fluxo_com(v1)
    v2 = custom_blocks.nova_versao(com_docker.store, com_docker.registro, com_docker.executor, v1["id"],
                                   declarar('def run(inputs, params):\n    return {"mensagem": "versão 2"}\n', entradas=[]))["block"]
    assert v2["version"] == 2 and v2["id"] == v1["id"]
    # o fluxo antigo continua exatamente igual
    assert com_docker.executar(f_antigo)["result"]["outputs"][0]["value"] == "versão 1"
    # um fluxo novo pode escolher a versão nova
    assert com_docker.executar(fluxo_com(v2))["result"]["outputs"][0]["value"] == "versão 2"
    # a biblioteca lista a mais nova, mas as duas continuam disponíveis
    assert {(t.id, t.version) for t in com_docker.registro.listar(todas_versoes=True) if t.id == v1["id"]} == {(v1["id"], 1), (v1["id"], 2)}
    # e o fluxo antigo avisa (sem mudar nada) que existe versão mais nova
    from app.validation import analisar
    a = analisar(Flow.model_validate(f_antigo), com_docker.registro.resolver, ultima_versao=com_docker.registro.ultima_versao)
    aviso = next(i for i in a.issues if i.code == "versao_desatualizada")
    assert aviso.severity == "aviso" and "v2" in aviso.message and "v1" in aviso.message


def test_bloco_em_uso_nao_pode_ser_excluido(com_docker):
    b = criar(com_docker, 'def run(inputs, params):\n    return {"mensagem": "x"}\n', entradas=[])
    com_docker.store.criar_projeto("Usa o bloco", "", fluxo_com(b))
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
    assert com_docker.store.listar_tipos() == []  # nada foi salvo


def test_declaracao_do_bloco_e_validada(com_docker):
    with pytest.raises(ValidationError):  # id de porta inválido
        declarar("def run(i, p):\n    return {}", saidas=[{"id": "Mensagem Ruim", "label": "x", "type": "texto"}])
    with pytest.raises(ValidationError):  # ids repetidos
        declarar("def run(i, p):\n    return {}", entradas=[{"id": "a", "label": "A"}, {"id": "a", "label": "B"}])
    with pytest.raises(ApiError):  # sem nenhuma saída
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
    falha = com_docker.motor.testar_bloco(tipo, {}, {"nome": "Lia"} | {})
    assert falha["state"] == "concluido"
    erro = com_docker.motor.testar_bloco(draft.model_copy(update={"code": 'def run(inputs, params):\n    return {"mensagem": 1/0}'}).para_tipo("custom.rascunho", 1), {}, {"nome": "x"})
    assert erro["state"] == "falhou" and erro["error"]["line"] == 2


def test_para_cada_com_codigo_python_aplica_a_funcao_a_todos_os_itens(com_docker):
    codigo = "def transformar(item, indice):\n    return {'n': item, 'quadrado': item * item, 'pos': indice}\n"
    f = fluxo([constante("l", "lista", [1, 2, 3, 4]),
               bloco("cada", "builtin.para_cada", {"operacao": "python", "codigo": codigo, "limite": 10}), saida()],
              [con("c1", "l", "valor", "cada", "lista"), con("c2", "cada", "resultado", "saida", "valor")])
    run = com_docker.executar(f)
    assert run["result"]["outputs"][0]["value"] == [{"n": n, "quadrado": n * n, "pos": n - 1} for n in (1, 2, 3, 4)]
    f["blocks"][1]["params"]["codigo"] = "def transformar(item):\n    return 10 // item\n"
    f["blocks"][0]["params"]["valor"] = [5, 0, 1]
    run = com_docker.executar(f)
    erro = run["error"]
    assert erro["code"] == "excecao_python" and "No item 2" in erro["message"] and erro["line"] == 2


def test_dois_blocos_python_encadeados_rodam_em_contêineres_separados(com_docker):
    b1 = criar(com_docker, 'def run(inputs, params):\n    open("/tmp/segredo", "w").write("x")\n    return {"mensagem": "a"}\n', entradas=[])
    b2 = criar(com_docker,
               'def run(inputs, params):\n    try:\n        open("/tmp/segredo")\n        return {"mensagem": "vazou"}\n    except OSError:\n        return {"mensagem": "isolado"}\n',
               entradas=[{"id": "nome", "label": "Nome", "type": "texto"}])
    f = fluxo([bloco("p1", b1["id"], versao=1), bloco("p2", b2["id"], versao=1), saida()],
              [con("c1", "p1", "mensagem", "p2", "nome"), con("c2", "p2", "mensagem", "saida", "valor")])
    assert com_docker.executar(f)["result"]["outputs"][0]["value"] == "isolado"
