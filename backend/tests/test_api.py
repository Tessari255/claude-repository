"""API HTTP: persistência, salvamento seguro, exportar/importar, execuções, cancelamento, modelos, proteções do
serviço local, indisponibilidade do executor e os modelos entregues."""

from __future__ import annotations

import dataclasses
import json
import threading
import time

import pytest

from .conftest import LIMITES_TESTE, cliente
from .helpers import (aguardar, campo, carregar_exemplo, compor, condicao, estados, etapas, fluxo, lit, matematica, passo, python_inline,
                      ref, repeticoes, saida)


def fluxo_completo():
    """Fluxo com condição, laço, escopo, rótulos, anotações, tentativas, unicode e conteúdo dinâmico em vários níveis."""
    dados = {"usuario": {"nome": "Ana", "tags": ["a", "b"]}, "n": 2.5, "ç": None}
    return fluxo(
        [passo("campo", "builtin.selecionar_campos", {"objeto": ref("gatilho", "d")}, {"caminhos": "usuario.nome\nn", "se_ausente": "nulo"},
               label="Pegar campos", note="anotação com ç e emoji 🙂", retry=2, intervalo=1.5),
         condicao("c", ref("campo", "valor"), "igual", "Ana", sim=[compor("k", lit([1, {"a": None}, "ç"]))],
                  nao=[passo("e", "builtin.escopo", slots={"corpo": [compor("z", lit(1))]})]),
         saida("saida", "Nome", ref("campo", "valor")), saida("saida2", "Tudo", ref("campo", "selecionados"), run_after=["sucesso", "falhou"])],
        [campo("d", "json", dados)])


# ------------------------------------------------------------------------------ persistência
def test_criar_salvar_fechar_e_reabrir_preserva_passos_parametros_e_conteudo_dinamico(settings, executor):
    f = fluxo_completo()
    with cliente(settings, executor) as c1:
        p = c1.post("/api/projetos", json={"name": "Meu projeto", "description": "teste"}).json()
        assert p["flow"]["steps"] == [] and p["flow"]["trigger"]["id"] == "gatilho" and p["revision"] == 1
        salvo = c1.put(f"/api/projetos/{p['id']}", json={"flow": f, "base_revision": 1})
        assert salvo.status_code == 200 and salvo.json()["revision"] == 2
    with cliente(settings, executor) as c2:  # "fecha" a aplicação e reabre um app novo sobre o mesmo banco
        lista = c2.get("/api/projetos").json()
        assert [(x["name"], x["step_count"], x["run_count"], x["last_run_state"]) for x in lista] == [("Meu projeto", 7, 0, None)]
        reaberto = c2.get(f"/api/projetos/{p['id']}").json()
    assert reaberto["name"] == "Meu projeto" and reaberto["description"] == "teste"
    # idêntico ao que foi enviado, depois de normalizado pelo servidor (padrões preenchidos)
    from app.models import Flow
    assert reaberto["flow"] == Flow.model_validate(f).model_dump()
    assert Flow.model_validate(reaberto["flow"]).model_dump() == reaberto["flow"]  # e relê igual


def test_rascunho_com_campos_obrigatorios_faltando_pode_ser_salvo(client):
    p = client.post("/api/projetos", json={"name": "Rascunho"}).json()
    rascunho = fluxo([passo("m", "builtin.matematica")])
    assert client.put(f"/api/projetos/{p['id']}", json={"flow": rascunho}).status_code == 200
    r = client.post("/api/fluxos/validar", json={"flow": rascunho}).json()
    assert r["valid"] is False and r["issues"][0]["code"] == "campo_obrigatorio" and r["issues"][0]["step_id"] == "m"
    assert r["port_types"]["m"]["inputs"] == {"a": "numero", "b": "numero"}


def test_referencia_quebrada_tambem_e_um_rascunho_salvavel(client):
    p = client.post("/api/projetos", json={"name": "x"}).json()
    quebrado = fluxo([compor("a", ref("apagado", "resultado"))])
    assert client.put(f"/api/projetos/{p['id']}", json={"flow": quebrado}).status_code == 200
    v = client.post("/api/fluxos/validar", json={"flow": quebrado}).json()
    assert not v["valid"] and v["issues"][0]["code"] == "referencia_invalida"


def test_salvar_com_erro_de_estrutura_nao_perde_o_ultimo_fluxo_valido(client):
    p = client.post("/api/projetos", json={"name": "Seguro"}).json()
    valido = fluxo_completo()
    client.put(f"/api/projetos/{p['id']}", json={"flow": valido})
    duplicado = fluxo([compor("a", lit(1)), compor("a", lit(2))])
    r = client.put(f"/api/projetos/{p['id']}", json={"flow": duplicado, "name": "Novo nome"})
    assert r.status_code == 422
    erro = r.json()["error"]
    assert erro["code"] == "fluxo_invalido" and "último fluxo salvo foi mantido" in erro["message"]
    assert erro["issues"][0]["code"] == "passo_duplicado"
    atual = client.get(f"/api/projetos/{p['id']}").json()
    assert atual["name"] == "Seguro" and atual["revision"] == 2 and len(atual["flow"]["steps"]) == 4  # nada mudou


def test_estrutura_invalida_no_json_e_recusada_com_mensagem_em_portugues(client):
    p = client.post("/api/projetos", json={"name": "x"}).json()
    r = client.put(f"/api/projetos/{p['id']}", json={"flow": {"steps": [{"id": "a"}]}})
    assert r.status_code == 422
    erro = r.json()["error"]
    assert erro["code"] == "requisicao_invalida" and erro["message"] == "Os dados enviados não são válidos."
    assert any("campo obrigatório ausente" in i["message"] for i in erro["issues"])
    antigo = {"schema_version": 1, "blocks": [], "connections": []}  # o formato antigo não é aceito na API de salvar
    assert client.put(f"/api/projetos/{p['id']}", json={"flow": antigo}).status_code == 422


def test_conflito_de_revisao_evita_sobrescrever_alteracao_de_outra_aba(client):
    p = client.post("/api/projetos", json={"name": "x"}).json()
    assert client.put(f"/api/projetos/{p['id']}", json={"name": "A", "base_revision": 1}).status_code == 200
    r = client.put(f"/api/projetos/{p['id']}", json={"name": "B", "base_revision": 1})
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflito_de_revisao"
    assert client.get(f"/api/projetos/{p['id']}").json()["name"] == "A"


def test_projeto_inexistente_e_exclusao(client):
    assert client.get("/api/projetos/nao_existe").status_code == 404
    p = client.post("/api/projetos", json={"name": "x"}).json()
    assert client.delete(f"/api/projetos/{p['id']}").status_code == 204
    assert client.get(f"/api/projetos/{p['id']}").status_code == 404


# ------------------------------------------------------------------------------ execuções
def projeto_soma(c):
    return c.post("/api/projetos", json={"name": "Soma", "flow": fluxo(
        [matematica("s", ref("gatilho", "a"), ref("gatilho", "b")), saida("o", "Soma", ref("s", "resultado"))],
        [campo("a", "numero", 2), campo("b", "numero", 3)])}).json()


def test_executar_pela_api_acompanhar_e_consultar_historico(client):
    p = projeto_soma(client)
    r = client.post(f"/api/projetos/{p['id']}/execucoes", json={})
    assert r.status_code == 202
    inicial = r.json()
    assert inicial["state"] in ("aguardando", "executando", "concluido") and {s["step_id"] for s in inicial["steps"]} == {"gatilho", "s", "o"}
    run = aguardar(client, inicial["id"])
    assert run["state"] == "concluido" and run["result"]["outputs"][0]["value"] == 5
    hist = client.get(f"/api/projetos/{p['id']}/execucoes").json()
    assert [h["id"] for h in hist] == [inicial["id"]] and hist[0]["duration_ms"] is not None
    resumo = client.get("/api/projetos").json()[0]
    assert resumo["run_count"] == 1 and resumo["last_run_state"] == "concluido" and resumo["last_run_at"]
    assert client.get("/api/execucoes/exe_inexistente").status_code == 404


def test_dados_do_gatilho_pela_api_e_validacao_deles(client):
    p = projeto_soma(client)
    run = aguardar(client, client.post(f"/api/projetos/{p['id']}/execucoes", json={"trigger_inputs": {"a": 40, "b": 2}}).json()["id"])
    assert run["result"]["outputs"][0]["value"] == 42 and run["trigger_inputs"] == {"a": 40, "b": 2}
    r = client.post(f"/api/projetos/{p['id']}/execucoes", json={"trigger_inputs": {"a": "x"}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "dados_invalidos" and "deveria ser número" in r.json()["error"]["issues"][0]["message"]
    r = client.post(f"/api/projetos/{p['id']}/execucoes", json={"trigger_inputs": {"zzz": 1}})
    assert r.status_code == 422 and len(client.get(f"/api/projetos/{p['id']}/execucoes").json()) == 1  # só a válida foi registrada


def test_executar_usa_o_fluxo_do_editor_mesmo_sem_salvar_e_rejeita_fluxo_invalido(client):
    p = client.post("/api/projetos", json={"name": "x"}).json()
    nao_salvo = fluxo([compor("a", lit(7)), saida("o", "x", ref("a", "resultado"))])
    run = aguardar(client, client.post(f"/api/projetos/{p['id']}/execucoes", json={"flow": nao_salvo}).json()["id"])
    assert run["result"]["outputs"][0]["value"] == 7
    assert client.get(f"/api/projetos/{p['id']}").json()["flow"]["steps"] == []  # nada foi salvo por baixo dos panos
    r = client.post(f"/api/projetos/{p['id']}/execucoes", json={"flow": fluxo([passo("m", "builtin.matematica")])})
    assert r.status_code == 422 and r.json()["error"]["code"] == "fluxo_invalido"
    assert r.json()["error"]["issues"][0]["code"] == "campo_obrigatorio"
    assert len(client.get(f"/api/projetos/{p['id']}/execucoes").json()) == 1


def test_historico_guarda_o_fluxo_da_execucao_e_as_repeticoes_de_cada_passo(client):
    f = fluxo([passo("laco", "builtin.para_cada", {"lista": lit([1, 2, 3])}, {"limite": 5}, slots={"corpo": [compor("x", ref("laco", "item"))]})])
    p = client.post("/api/projetos", json={"name": "laço", "flow": f}).json()
    run = aguardar(client, client.post(f"/api/projetos/{p['id']}/execucoes", json={}).json()["id"])
    assert sorted(repeticoes(run, "x")) == [(0,), (1,), (2,)]
    assert [s["iteration"] for s in run["steps"] if s["step_id"] == "x"] == [[0], [1], [2]]


def test_cancelar_uma_execucao_em_andamento(client):
    f = fluxo([matematica("quebra", lit(1), lit(0), "dividir", retry=5, intervalo=30)])
    p = client.post("/api/projetos", json={"name": "lenta", "flow": f}).json()
    rid = client.post(f"/api/projetos/{p['id']}/execucoes", json={}).json()["id"]
    for _ in range(100):
        if {s["step_id"]: s["state"] for s in client.get(f"/api/execucoes/{rid}").json()["steps"]}["quebra"] == "executando":
            break
        time.sleep(0.05)
    r = client.post(f"/api/execucoes/{rid}/cancelar")
    assert r.status_code == 202 and r.json() == {"cancelling": True}
    run = aguardar(client, rid, timeout=10)
    assert run["state"] == "cancelado"
    assert client.post(f"/api/execucoes/{rid}/cancelar").status_code == 409  # já terminou
    assert client.post("/api/execucoes/exe_nao_existe/cancelar").status_code == 404


def test_execucoes_em_andamento_quando_o_servidor_cai_sao_marcadas_como_falha(settings, executor):
    from app.models import Flow
    with cliente(settings, executor) as c1:
        p = c1.post("/api/projetos", json={"name": "x", "flow": fluxo([compor("a", lit(1)), saida("o", "x", ref("a", "resultado"))])}).json()
        svc = c1.app.state.servicos
        rid = svc.motor.preparar(Flow.model_validate(p["flow"]), p["id"])
        svc.store.atualizar_execucao(rid, state="executando")  # simula queda no meio da execução
    with cliente(settings, executor) as c2:
        run = c2.get(f"/api/execucoes/{rid}").json()
    assert run["state"] == "falhou" and run["error"]["code"] == "execucao_interrompida"
    assert {s["state"] for s in run["steps"]} == {"ignorado"}


# ------------------------------------------------------------------------------ exportar e importar
def test_exportar_e_importar_preserva_o_comportamento_inclusive_blocos_python(settings, executor, tmp_path):
    bloco = {"name": "Saudar", "description": "diz olá", "category": "Meus blocos",
             "inputs": [{"id": "nome", "label": "Nome", "type": "texto"}], "outputs": [{"id": "mensagem", "label": "Mensagem", "type": "texto"}],
             "params": [], "code": 'def run(inputs, params):\n    return {"mensagem": "Olá, " + inputs["nome"] + "!"}\n'}
    with cliente(settings, executor) as origem:
        b = origem.post("/api/blocos", json=bloco).json()["block"]
        f = fluxo([passo("p", b["id"], {"nome": ref("gatilho", "nome")}), saida("s", "Mensagem", ref("p", "mensagem"))], [campo("nome", "texto", "Ana")])
        p = origem.post("/api/projetos", json={"name": "Usa o bloco", "description": "d", "flow": f}).json()
        run1 = aguardar(origem, origem.post(f"/api/projetos/{p['id']}/execucoes", json={}).json()["id"])
        arquivo = origem.post("/api/fluxos/exportar", json={"name": p["name"], "description": p["description"], "flow": p["flow"]})
        assert arquivo.status_code == 200
        envelope = arquivo.json()
    assert envelope["format"] == "trama.fluxo" and envelope["format_version"] == 2
    assert [x["id"] for x in envelope["custom_blocks"]] == [b["id"]] and "Olá" in envelope["custom_blocks"][0]["code"]  # o código viaja junto
    texto = json.dumps(envelope, ensure_ascii=False)

    destino_settings = dataclasses.replace(settings, data_dir=tmp_path / "outra_instalacao")
    with cliente(destino_settings, executor) as destino:
        assert not [x for x in destino.get("/api/blocos").json() if x["id"].startswith("custom.")]
        res = destino.post("/api/projetos/importar", json=json.loads(texto))
        assert res.status_code == 201
        novo = res.json()
        assert novo["flow"] == p["flow"]  # a estrutura é idêntica
        assert [x["id"] for x in destino.get("/api/blocos").json() if x["id"].startswith("custom.")] == [b["id"]]
        run2 = aguardar(destino, destino.post(f"/api/projetos/{novo['id']}/execucoes", json={}).json()["id"])
        assert destino.post("/api/projetos/importar", json=json.loads(texto)).json()["created_blocks"] == []  # importar de novo reaproveita
    assert run1["state"] == run2["state"] == "concluido"
    assert run1["result"]["outputs"] == run2["result"]["outputs"] == [{"step_id": "s", "title": "Mensagem", "value": "Olá, Ana!"}]


def envelope_com_bloco(client):
    corpo = {"name": "Saudar", "inputs": [{"id": "nome", "label": "Nome", "type": "texto"}], "outputs": [{"id": "mensagem", "label": "M", "type": "texto"}],
             "params": [], "code": 'def run(inputs, params):\n    return {"mensagem": "a"}\n'}
    b = client.post("/api/blocos", json=corpo).json()["block"]
    f = fluxo([passo("e", "builtin.escopo", slots={"corpo": [passo("p", b["id"], {"nome": lit("x")})]})])  # bloco usado DENTRO de um escopo
    env = client.post("/api/fluxos/exportar", json={"name": "x", "flow": f}).json()
    client.delete(f"/api/blocos/{b['id']}")
    return env, b


def test_blocos_usados_dentro_de_escopos_tambem_viajam_no_arquivo(client):
    env, b = envelope_com_bloco(client)
    assert [x["id"] for x in env["custom_blocks"]] == [b["id"]]


def test_importar_bloco_com_mesmo_id_e_codigo_diferente_vira_copia_sem_alterar_o_existente(client):
    env, b = envelope_com_bloco(client)
    assert client.post("/api/projetos/importar", json=env).status_code == 201  # instala o bloco
    adulterado = json.loads(json.dumps(env))
    adulterado["custom_blocks"][0]["code"] = 'def run(inputs, params):\n    return {"mensagem": "ALTERADO"}\n'
    r = client.post("/api/projetos/importar", json=adulterado)
    assert r.status_code == 201
    corpo = r.json()
    assert any("cópia separada" in w for w in corpo["warnings"])
    usado = corpo["flow"]["steps"][0]["slots"]["corpo"][0]["type"]
    assert usado != b["id"]  # o fluxo importado aponta para a cópia
    blocos = {x["id"]: x for x in client.get("/api/blocos").json() if x["id"].startswith("custom.")}
    assert '"a"' in blocos[b["id"]]["code"] and len(blocos) == 2  # o original ficou intacto


@pytest.mark.parametrize("mutar,trecho", [
    (lambda e: e.update(format="outra.coisa"), "não é um fluxo válido"),
    (lambda e: e.pop("flow"), "não é um fluxo válido"),
    (lambda e: e.update(surpresa=1), "não é um fluxo válido"),
    (lambda e: e["flow"]["steps"][0].update(version=99), "não está disponível"),
    (lambda e: e["flow"].update(schema_version=7), "não é um fluxo válido"),
    (lambda e: e.update(format_version=9), "não é um fluxo válido"),
    (lambda e: e["custom_blocks"].clear(), "não o inclui"),
    (lambda e: e["flow"]["steps"].append(e["flow"]["steps"][0]), "problemas"),
    (lambda e: e["custom_blocks"][0].update(kind="builtin"), "não é um fluxo válido"),
])
def test_importacao_rejeita_conteudo_invalido_e_nao_grava_nada(client, mutar, trecho):
    f = fluxo([passo("p", "custom.saudar", {"nome": lit("x")}, versao=1)])
    envelope = {"format": "trama.fluxo", "format_version": 2, "project": {"name": "x"}, "flow": f,
                "custom_blocks": [{"id": "custom.saudar", "version": 1, "name": "S", "kind": "python", "category": "P", "description": "",
                                   "inputs": [{"id": "nome", "label": "N", "type": "texto"}], "outputs": [{"id": "m", "label": "M", "type": "texto"}],
                                   "params": [], "code": 'def run(i, p):\n    return {"m": "a"}\n'}]}
    mutar(envelope)
    r = client.post("/api/projetos/importar", json=envelope)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["message"]
    assert client.get("/api/projetos").json() == []
    assert [b for b in client.get("/api/blocos").json() if b["id"].startswith("custom.")] == []


def test_importacao_nao_confia_no_formato_do_arquivo(client):
    assert client.post("/api/projetos/importar", json=[1, 2, 3]).status_code == 422
    r = client.post("/api/projetos/importar", content=b"isto nao e json", headers={"content-type": "application/json"})
    assert r.status_code == 422
    r = client.post("/api/projetos/importar/validar", json=carregar_exemplo("02-soma.json"))
    assert r.status_code == 200 and r.json()["step_count"] == 2


# ------------------------------------------------------------------------------ modelos
def test_modelos_listam_os_exemplos_prontos_para_importar(client):
    modelos = client.get("/api/modelos").json()
    assert [m["id"] for m in modelos] == ["01-saudacao", "02-soma", "03-condicao", "04-lista", "05-tratar-erros", "06-laco-e-variavel"]
    soma = next(m for m in modelos if m["id"] == "02-soma")
    assert soma["name"].startswith("Exemplo 2") and soma["step_count"] == 2 and soma["description"]
    r = client.post("/api/projetos/importar", json=soma["file"])  # o arquivo do modelo é o mesmo formato da importação
    assert r.status_code == 201 and r.json()["name"] == soma["name"]


# ------------------------------------------------------------------------------ proteção do serviço local
def test_requisicao_de_outra_origem_e_conteudo_nao_json_sao_recusados(client):
    r = client.post("/api/projetos", json={"name": "x"}, headers={"origin": "https://site-malicioso.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "origem_nao_permitida"
    r = client.post("/api/projetos", content='{"name": "x"}', headers={"content-type": "text/plain"})
    assert r.status_code == 415
    assert client.post("/api/projetos", json={"name": "x"}, headers={"origin": "http://testserver"}).status_code == 201
    assert client.get("/api/projetos", headers={"host": "invasor.example"}).status_code == 400
    assert len(client.get("/api/projetos").json()) == 1  # só a requisição legítima criou algo
    assert client.post("/api/execucoes/exe_x/cancelar", headers={"origin": "https://site-malicioso.example"}).status_code == 403


# ------------------------------------------------------------------------------ blocos via API
def test_catalogo_de_blocos_traz_gatilho_controles_variaveis_e_python(client):
    por_id = {b["id"]: b for b in client.get("/api/blocos").json()}
    assert por_id["builtin.gatilho_manual"]["trigger"] is True
    assert [s["id"] for s in por_id["builtin.condicao"]["slots"]] == ["sim", "nao"]
    assert por_id["builtin.escopo"]["slots"][0]["transparent"] is True
    assert {"builtin.para_cada", "builtin.repetir_ate", "builtin.encerrar", "builtin.var_inicializar", "builtin.var_definir",
            "builtin.var_incrementar", "builtin.var_acrescentar", "builtin.compor", "builtin.python", "builtin.saida"} <= set(por_id)
    assert por_id["builtin.python"]["inputs_from"] == "entradas" and por_id["builtin.python"]["category"] == "Python"
    assert not any(k in por_id for k in ("builtin.inicio", "builtin.constante"))  # blocos do formato antigo não existem mais


def test_ciclo_de_vida_do_bloco_personalizado_pela_api(client):
    corpo = {"name": "Dobro", "description": "dobra", "category": "Meus blocos",
             "inputs": [{"id": "n", "label": "N", "type": "numero"}],
             "outputs": [{"id": "r", "label": "R", "type": "numero"}], "params": [],
             "code": 'def run(inputs, params):\n    return {"r": inputs["n"] * 2}\n'}
    r = client.post("/api/blocos", json=corpo)
    assert r.status_code == 201
    b = r.json()["block"]
    assert b["version"] == 1 and b["kind"] == "python" and r.json()["warnings"] == []
    corpo["code"] = 'def run(inputs, params):\n    return {"r": inputs["n"] * 3}\n'
    v2 = client.put(f"/api/blocos/{b['id']}", json=corpo).json()["block"]
    assert v2["version"] == 2
    assert client.get(f"/api/blocos/{b['id']}/versoes/1").json()["code"].endswith("* 2}\n")
    assert [t["version"] for t in client.get("/api/blocos").json() if t["id"] == b["id"]] == [2]
    teste = client.post("/api/blocos/testar", json={"ref": {"type": b["id"], "version": 1}, "inputs": {"n": 5}}).json()
    assert teste["state"] == "concluido" and teste["steps"][0]["outputs"] == {"r": 10}
    assert client.get(f"/api/blocos/{b['id']}/exemplo").json()["inputs"] == {"n": 1}
    f = fluxo([passo("d", b["id"], {"n": lit(4)}, versao=1), saida("o", "x", ref("d", "r"))])
    proj = client.post("/api/projetos", json={"name": "usa", "flow": f}).json()
    r = client.delete(f"/api/blocos/{b['id']}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "bloco_em_uso"
    client.delete(f"/api/projetos/{proj['id']}")
    assert client.delete(f"/api/blocos/{b['id']}").status_code == 204
    assert client.delete("/api/blocos/builtin.matematica").status_code == 403


def test_verificar_codigo_e_teste_de_rascunho(client):
    ruim = client.post("/api/blocos/verificar", json={"code": "def run(inputs, params)\n    return {}"}).json()
    assert ruim["ok"] is False and ruim["error"]["technical"]["line"] == 1 and "sintaxe" in ruim["error"]["message"]
    assert client.post("/api/blocos/verificar", json={"code": "def run(inputs, params):\n    return {}"}).json()["ok"] is True
    draft = {"name": "Rascunho", "inputs": [], "outputs": [{"id": "x", "label": "X", "type": "numero"}], "params": [],
             "code": 'def run(inputs, params):\n    return {"x": 1}\n'}
    run = client.post("/api/blocos/testar", json={"draft": draft}).json()
    assert run["state"] == "concluido" and run["steps"][0]["outputs"] == {"x": 1}
    assert client.post("/api/blocos/testar", json={"draft": draft, "ref": {"type": "builtin.matematica", "version": 1}}).status_code == 422
    r = client.post("/api/blocos", json={**draft, "outputs": [{"id": "x", "label": "X"}, {"id": "x", "label": "Y"}]})
    assert r.status_code == 422 and "repetidos" in json.dumps(r.json(), ensure_ascii=False)


# ------------------------------------------------------------------------------ executor
def test_sistema_informa_o_executor_disponivel(client):
    s = client.get("/api/sistema").json()
    assert s["executor"]["disponivel"] is True and s["limits"]["time_s"] == LIMITES_TESTE.tempo_s


def test_sem_executor_a_dependencia_e_informada_e_o_python_fica_desabilitado(client_sem_docker):
    c = client_sem_docker
    ex = c.get("/api/sistema").json()["executor"]
    assert ex["disponivel"] is False and ex["motivo"] == "docker_ausente" and "Instale o Docker" in ex["instrucao"]
    corpo = {"name": "B", "inputs": [], "outputs": [{"id": "x", "label": "X", "type": "numero"}], "params": [],
             "code": 'def run(inputs, params):\n    return {"x": 1}\n'}
    salvo = c.post("/api/blocos", json=corpo)  # editar/salvar continua possível, com aviso
    assert salvo.status_code == 201 and "executor isolado está indisponível" in salvo.json()["warnings"][0]
    b = salvo.json()["block"]
    teste = c.post("/api/blocos/testar", json={"ref": {"type": b["id"], "version": 1}}).json()
    assert teste["state"] == "falhou" and teste["error"]["code"] == "executor_indisponivel" and teste["steps"][0]["outputs"] is None
    for passo_python in (passo("p", b["id"], versao=1), python_inline("p", "def run(i, p):\n    return {'resultado': 'x'}")):
        f = fluxo([passo_python, saida("o", "x", lit(1))])
        p = c.post("/api/projetos", json={"name": "x", "flow": f}).json()
        r = c.post(f"/api/projetos/{p['id']}/execucoes", json={})
        assert r.status_code == 422 and [i["code"] for i in r.json()["error"]["issues"]] == ["executor_indisponivel"]
        v = c.post("/api/fluxos/validar", json={"flow": f}).json()
        assert v["valid"] is False and any(i["code"] == "executor_indisponivel" for i in v["issues"])
    soma = fluxo([compor("a", lit(1)), saida("o", "x", ref("a", "resultado"))])  # blocos internos continuam funcionando
    p2 = c.post("/api/projetos", json={"name": "y", "flow": soma}).json()
    assert aguardar(c, c.post(f"/api/projetos/{p2['id']}/execucoes", json={}).json()["id"])["state"] == "concluido"


# ------------------------------------------------------------------------------ modelos entregues
def test_modelos_sao_criados_na_primeira_execucao_e_produzem_os_resultados_documentados(settings, executor):
    with cliente(dataclasses.replace(settings, semear_exemplos=True), executor) as c:
        projetos = {p["name"].split(" — ")[0]: p for p in c.get("/api/projetos").json()}
        assert len(projetos) == 6
        resultados = {}
        for nome, p in projetos.items():
            run = aguardar(c, c.post(f"/api/projetos/{p['id']}/execucoes", json={}).json()["id"])
            assert run["state"] == "concluido", (nome, run["error"])
            resultados[nome] = ({o["title"]: o["value"] for o in run["result"]["outputs"]}, etapas(run))
    assert resultados["Exemplo 1"][0] == {"Saudação": "Olá, Ana!"}
    assert resultados["Exemplo 2"][0] == {"Soma": 5}
    saidas, e = resultados["Exemplo 3"]
    assert saidas == {"Valor com desconto": 225} and e["saida_nao"]["state"] == "ignorado" and e["desconto"]["state"] == "concluido"
    assert resultados["Exemplo 4"][0] == {"Lista transformada": [30, 60, 90]}
    assert resultados["Exemplo 5"][0]["Aviso"].startswith("Não foi possível calcular") and resultados["Exemplo 5"][1]["tentar"]["state"] == "falhou"
    assert resultados["Exemplo 6"][0] == {"Soma": 60, "Média": 20}
    with cliente(dataclasses.replace(settings, semear_exemplos=True), executor) as c:  # não duplica ao reiniciar
        assert len(c.get("/api/projetos").json()) == 6
