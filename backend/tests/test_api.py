"""API HTTP: persistência (critério 1), salvamento seguro, export/import (critério 10),
proteções do serviço local, indisponibilidade do executor e os exemplos entregues."""

from __future__ import annotations

import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from app.main import criar_app
from app.sandbox import DockerExecutor

from .conftest import IMAGEM, LIMITES_TESTE
from .helpers import aguardar, bloco, carregar_exemplo, con, constante, etapas, fluxo, saida


def cliente(settings, executor):
    return TestClient(criar_app(settings, executor))


@pytest.fixture()
def client(settings, executor):
    with cliente(settings, executor) as c:
        yield c


@pytest.fixture()
def client_sem_docker(settings):
    ex = DockerExecutor(IMAGEM, LIMITES_TESTE, docker_bin="docker-que-nao-existe-xyz")
    with cliente(settings, ex) as c:
        yield c


def fluxo_completo():
    """Fluxo com posições fracionárias, parâmetros aninhados, rótulos, viewport e várias conexões."""
    f = fluxo(
        [bloco("inicio", "builtin.inicio", {"dados": {"usuario": {"nome": "Ana", "tags": ["a", "b"]}, "n": 2.5}}, x=-120.5, y=40.25),
         bloco("campo", "builtin.selecionar_campos", {"caminhos": "usuario.nome\nn", "se_ausente": "nulo"}, x=200, y=-30, label="Pegar campos"),
         constante("k", "lista", [1, {"a": None}, "ç"], x=200, y=300),
         saida("saida", "Nome", x=520, y=10.75), saida("saida2", "Tudo", x=520, y=200)],
        [con("c1", "inicio", "dados", "campo", "objeto"), con("c2", "campo", "valor", "saida", "valor"),
         con("c3", "campo", "selecionados", "saida2", "valor")])
    f["viewport"] = {"x": -30.5, "y": 12, "zoom": 0.75}
    return f


# ------------------------------------------------------------------------------ critério 1
def test_criterio_1_criar_salvar_fechar_e_reabrir_preserva_blocos_posicoes_parametros_e_conexoes(settings, executor):
    f = fluxo_completo()
    with cliente(settings, executor) as c1:
        p = c1.post("/api/projetos", json={"name": "Meu projeto", "description": "teste"}).json()
        assert p["flow"]["blocks"] == [] and p["revision"] == 1
        salvo = c1.put(f"/api/projetos/{p['id']}", json={"flow": f, "base_revision": 1})
        assert salvo.status_code == 200 and salvo.json()["revision"] == 2
    # "fecha" a aplicação (o with encerrou o app) e reabre um app novo sobre o mesmo banco
    with cliente(settings, executor) as c2:
        lista = c2.get("/api/projetos").json()
        assert [(x["name"], x["block_count"]) for x in lista] == [("Meu projeto", 5)]
        reaberto = c2.get(f"/api/projetos/{p['id']}").json()
    assert reaberto["flow"] == f  # idêntico, inclusive posições fracionárias, aninhamento, unicode e viewport
    assert reaberto["name"] == "Meu projeto" and reaberto["description"] == "teste"


def test_rascunho_com_campos_obrigatorios_faltando_pode_ser_salvo(client):
    p = client.post("/api/projetos", json={"name": "Rascunho"}).json()
    rascunho = fluxo([bloco("m", "builtin.matematica")])  # entradas sem ligação
    assert client.put(f"/api/projetos/{p['id']}", json={"flow": rascunho}).status_code == 200
    r = client.post("/api/fluxos/validar", json={"flow": rascunho}).json()
    assert r["valid"] is False and r["issues"][0]["code"] == "entrada_obrigatoria"


def test_salvar_com_erro_de_estrutura_nao_perde_o_ultimo_fluxo_valido(client):
    p = client.post("/api/projetos", json={"name": "Seguro"}).json()
    valido = fluxo_completo()
    client.put(f"/api/projetos/{p['id']}", json={"flow": valido})
    ciclo = fluxo([bloco("a", "builtin.matematica"), bloco("b", "builtin.matematica")],
                  [con("c1", "a", "resultado", "b", "a"), con("c2", "b", "resultado", "a", "a")])
    r = client.put(f"/api/projetos/{p['id']}", json={"flow": ciclo, "name": "Novo nome"})
    assert r.status_code == 422
    erro = r.json()["error"]
    assert erro["code"] == "fluxo_invalido" and "último fluxo salvo foi mantido" in erro["message"]
    assert erro["issues"][0]["code"] == "ciclo"
    atual = client.get(f"/api/projetos/{p['id']}").json()
    assert atual["flow"] == valido and atual["name"] == "Seguro" and atual["revision"] == 2  # nada mudou


def test_estrutura_invalida_no_json_e_recusada_com_mensagem_em_portugues(client):
    p = client.post("/api/projetos", json={"name": "x"}).json()
    r = client.put(f"/api/projetos/{p['id']}", json={"flow": {"blocks": [{"id": "a"}], "connections": []}})
    assert r.status_code == 422
    erro = r.json()["error"]
    assert erro["code"] == "requisicao_invalida" and erro["message"] == "Os dados enviados não são válidos."
    assert any("campo obrigatório ausente" in i["message"] for i in erro["issues"])


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


# ------------------------------------------------------------------------------ validação via API
def test_validar_conexao_rejeita_ciclo_e_tipo_incompativel_antes_de_criar(client):
    f = fluxo([bloco("a", "builtin.matematica"), bloco("b", "builtin.matematica"), constante("t", "texto", "x")],
              [con("c1", "a", "resultado", "b", "a")])
    ciclo = client.post("/api/fluxos/validar-conexao", json={"flow": f, "connection": con("c2", "b", "resultado", "a", "b")}).json()
    assert ciclo["ok"] is False and ciclo["issues"][0]["code"] == "ciclo" and "ciclo" in ciclo["issues"][0]["message"]
    tipo = client.post("/api/fluxos/validar-conexao", json={"flow": f, "connection": con("c3", "t", "valor", "b", "b")}).json()
    assert tipo["ok"] is False and tipo["issues"][0]["code"] == "tipo_incompativel"
    ok = client.post("/api/fluxos/validar-conexao", json={"flow": f, "connection": con("c4", "a", "resultado", "b", "b")}).json()
    assert ok == {"ok": True, "issues": []}


# ------------------------------------------------------------------------------ execução via API
def test_executar_pela_api_acompanhar_e_consultar_historico(client):
    p = client.post("/api/projetos", json={"name": "Soma", "flow": fluxo(
        [constante("a", "numero", 2), constante("b", "numero", 3), bloco("s", "builtin.matematica"), saida("o", "Soma")],
        [con("c1", "a", "valor", "s", "a"), con("c2", "b", "valor", "s", "b"), con("c3", "s", "resultado", "o", "valor")])}).json()
    r = client.post(f"/api/projetos/{p['id']}/execucoes", json={})
    assert r.status_code == 202
    inicial = r.json()
    assert inicial["state"] in ("aguardando", "executando", "concluido") and len(inicial["steps"]) == 4
    run = aguardar(client, inicial["id"])
    assert run["state"] == "concluido" and run["result"]["outputs"][0]["value"] == 5
    hist = client.get(f"/api/projetos/{p['id']}/execucoes").json()
    assert [h["id"] for h in hist] == [inicial["id"]] and hist[0]["duration_ms"] is not None
    assert client.get("/api/execucoes/exe_inexistente").status_code == 404


def test_executar_usa_o_fluxo_do_editor_mesmo_sem_salvar_e_rejeita_fluxo_invalido(client):
    p = client.post("/api/projetos", json={"name": "x"}).json()
    nao_salvo = fluxo([constante("a", "numero", 7), saida("o")], [con("c1", "a", "valor", "o", "valor")])
    run = aguardar(client, client.post(f"/api/projetos/{p['id']}/execucoes", json={"flow": nao_salvo}).json()["id"])
    assert run["result"]["outputs"][0]["value"] == 7
    assert client.get(f"/api/projetos/{p['id']}").json()["flow"]["blocks"] == []  # nada foi salvo por baixo dos panos
    invalido = fluxo([bloco("m", "builtin.matematica")])
    r = client.post(f"/api/projetos/{p['id']}/execucoes", json={"flow": invalido})
    assert r.status_code == 422 and r.json()["error"]["code"] == "fluxo_invalido"
    assert r.json()["error"]["issues"][0]["code"] == "entrada_obrigatoria"
    assert len(client.get(f"/api/projetos/{p['id']}/execucoes").json()) == 1  # só a execução válida foi registrada


def test_dados_iniciais_por_execucao(client):
    f = fluxo([bloco("i", "builtin.inicio", {"dados": {"x": 1}}), saida("o")], [con("c1", "i", "dados", "o", "valor")])
    p = client.post("/api/projetos", json={"name": "x", "flow": f}).json()
    run = aguardar(client, client.post(f"/api/projetos/{p['id']}/execucoes", json={"initial_data": {"i": {"x": 42}}}).json()["id"])
    assert run["result"]["outputs"][0]["value"] == {"x": 42}


def test_execucoes_em_andamento_quando_o_servidor_cai_sao_marcadas_como_falha(settings, executor):
    with cliente(settings, executor) as c1:
        p = c1.post("/api/projetos", json={"name": "x", "flow": fluxo([constante("a", "numero", 1), saida("o")], [con("c", "a", "valor", "o", "valor")])}).json()
        svc = c1.app.state.servicos
        rid = svc.motor.preparar(__import__("app.models", fromlist=["Flow"]).Flow.model_validate(p["flow"]), p["id"])
        svc.store.atualizar_execucao(rid, state="executando")  # simula queda no meio da execução
    with cliente(settings, executor) as c2:
        run = c2.get(f"/api/execucoes/{rid}").json()
    assert run["state"] == "falhou" and run["error"]["code"] == "execucao_interrompida"
    assert {s["state"] for s in run["steps"]} == {"ignorado"}


# ------------------------------------------------------------------------------ critério 10
def test_criterio_10_exportar_e_importar_preserva_o_comportamento(settings, executor, tmp_path):
    exemplo = carregar_exemplo("01-saudacao.json")
    with cliente(settings, executor) as origem:
        res = origem.post("/api/projetos/importar", json=exemplo)
        assert res.status_code == 201
        p = res.json()
        run1 = aguardar(origem, origem.post(f"/api/projetos/{p['id']}/execucoes", json={}).json()["id"])
        arquivo = origem.post("/api/fluxos/exportar", json={"name": p["name"], "description": p["description"], "flow": p["flow"]})
        assert arquivo.status_code == 200
        envelope = arquivo.json()
    assert envelope["format"] == "trama.fluxo" and [b["id"] for b in envelope["custom_blocks"]] == ["custom.saudacao"]
    assert "Olá" in envelope["custom_blocks"][0]["code"]  # o código viaja junto
    texto = json.dumps(envelope, ensure_ascii=False)  # como se fosse salvo em arquivo e lido de novo

    destino_settings = dataclasses.replace(settings, data_dir=tmp_path / "outra_instalacao")
    with cliente(destino_settings, executor) as destino:
        assert destino.get("/api/blocos").json() and not [b for b in destino.get("/api/blocos").json() if b["id"].startswith("custom.")]
        res = destino.post("/api/projetos/importar", json=json.loads(texto))
        assert res.status_code == 201
        novo = res.json()
        assert novo["flow"] == p["flow"]  # a estrutura é idêntica
        assert [b["id"] for b in destino.get("/api/blocos").json() if b["id"].startswith("custom.")] == ["custom.saudacao"]
        run2 = aguardar(destino, destino.post(f"/api/projetos/{novo['id']}/execucoes", json={}).json()["id"])
        # importar de novo reaproveita o bloco (mesmo conteúdo): nada duplica
        assert destino.post("/api/projetos/importar", json=json.loads(texto)).json()["created_blocks"] == []
        assert len([b for b in destino.get("/api/blocos?todas_versoes=true").json() if b["id"].startswith("custom.")]) == 1
    assert run1["state"] == run2["state"] == "concluido"
    assert run1["result"]["outputs"] == run2["result"]["outputs"] == [{"block_id": "saida", "title": "Mensagem", "value": "Olá, Ana!"}]
    assert [(s["block_id"], s["state"], s["outputs"]) for s in run1["steps"]] == [(s["block_id"], s["state"], s["outputs"]) for s in run2["steps"]]


def test_importar_bloco_com_mesmo_id_e_codigo_diferente_vira_copia_sem_alterar_o_existente(client):
    original = carregar_exemplo("01-saudacao.json")
    client.post("/api/projetos/importar", json=original)  # instala custom.saudacao v1
    adulterado = json.loads(json.dumps(original))
    adulterado["custom_blocks"][0]["code"] = 'def run(inputs, params):\n    return {"mensagem": "ALTERADO"}\n'
    r = client.post("/api/projetos/importar", json=adulterado)
    assert r.status_code == 201
    corpo = r.json()
    assert any("cópia separada" in w for w in corpo["warnings"])
    usados = {b["type"] for b in corpo["flow"]["blocks"] if b["type"].startswith("custom.")}
    assert usados and "custom.saudacao" not in usados  # o fluxo importado aponta para a cópia
    blocos = {b["id"]: b for b in client.get("/api/blocos").json() if b["id"].startswith("custom.")}
    assert "Olá" in blocos["custom.saudacao"]["code"] and len(blocos) == 2  # o original ficou intacto
    run = aguardar(client, client.post(f"/api/projetos/{corpo['id']}/execucoes", json={}).json()["id"])
    assert run["result"]["outputs"][0]["value"] == "ALTERADO"


@pytest.mark.parametrize("mutar,trecho", [
    (lambda e: e.update(format="outra.coisa"), "não é um fluxo válido"),
    (lambda e: e.pop("flow"), "não é um fluxo válido"),
    (lambda e: e.update(surpresa=1), "não é um fluxo válido"),
    (lambda e: e["flow"]["blocks"][0].update(version=99), "não está disponível"),
    (lambda e: e["flow"].update(schema_version=7), "não é um fluxo válido"),
    (lambda e: e["custom_blocks"].clear(), "não o inclui"),
    (lambda e: e["flow"]["connections"].append(
        {"id": "ciclo", "source": {"block": "saudar", "port": "mensagem"}, "target": {"block": "saudar", "port": "nome"}}), "problemas"),
    (lambda e: e["custom_blocks"][0].update(kind="builtin"), "não é um fluxo válido"),
])
def test_importacao_rejeita_conteudo_invalido_e_nao_grava_nada(client, mutar, trecho):
    envelope = carregar_exemplo("01-saudacao.json")
    mutar(envelope)
    r = client.post("/api/projetos/importar", json=envelope)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["message"]  # sempre em português
    assert client.get("/api/projetos").json() == []
    assert [b for b in client.get("/api/blocos").json() if b["id"].startswith("custom.")] == []


def test_importacao_nao_confia_no_formato_do_arquivo(client):
    assert client.post("/api/projetos/importar", json=[1, 2, 3]).status_code == 422
    r = client.post("/api/projetos/importar", content=b"isto nao e json", headers={"content-type": "application/json"})
    assert r.status_code == 422
    r = client.post("/api/projetos/importar/validar", json=carregar_exemplo("02-soma.json"))
    assert r.status_code == 200 and r.json()["block_count"] == 4


# ------------------------------------------------------------------------------ proteção do serviço local
def test_requisicao_de_outra_origem_e_conteudo_nao_json_sao_recusados(client):
    r = client.post("/api/projetos", json={"name": "x"}, headers={"origin": "https://site-malicioso.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "origem_nao_permitida"
    r = client.post("/api/projetos", content='{"name": "x"}', headers={"content-type": "text/plain"})
    assert r.status_code == 415
    mesma = client.post("/api/projetos", json={"name": "x"}, headers={"origin": "http://testserver"})
    assert mesma.status_code == 201
    assert client.get("/api/projetos", headers={"host": "invasor.example"}).status_code == 400  # host não permitido
    assert client.get("/api/projetos").json() != []
    assert len(client.get("/api/projetos").json()) == 1  # só a requisição legítima criou algo


# ------------------------------------------------------------------------------ blocos via API
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
    assert client.get(f"/api/blocos/{b['id']}/versoes/1").json()["code"].endswith("* 2}\n")  # a v1 segue lá
    atuais = [t for t in client.get("/api/blocos").json() if t["id"] == b["id"]]
    assert [t["version"] for t in atuais] == [2]
    teste = client.post("/api/blocos/testar", json={"ref": {"type": b["id"], "version": 1}, "inputs": {"n": 5}}).json()
    assert teste["state"] == "concluido" and teste["steps"][0]["outputs"] == {"r": 10}
    ex = client.get(f"/api/blocos/{b['id']}/exemplo").json()
    assert ex["inputs"] == {"n": 1}
    f = fluxo([constante("c", "numero", 4), bloco("d", b["id"], versao=1), saida("o")],
              [con("c1", "c", "valor", "d", "n"), con("c2", "d", "r", "o", "valor")])
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
    r = client.post("/api/blocos/testar", json={"draft": draft, "ref": {"type": "builtin.matematica", "version": 1}})
    assert r.status_code == 422
    r = client.post("/api/blocos", json={**draft, "outputs": [{"id": "x", "label": "X"}, {"id": "x", "label": "Y"}]})
    assert r.status_code == 422 and "repetidos" in json.dumps(r.json(), ensure_ascii=False)  # 422 claro, não 500


# ------------------------------------------------------------------------------ executor indisponível
def test_sistema_informa_o_executor_disponivel(client):
    s = client.get("/api/sistema").json()
    assert s["executor"]["disponivel"] is True and s["limits"]["time_s"] == LIMITES_TESTE.tempo_s


def test_sem_executor_a_dependencia_e_informada_e_o_codigo_personalizado_fica_desabilitado(client_sem_docker):
    c = client_sem_docker
    ex = c.get("/api/sistema").json()["executor"]
    assert ex["disponivel"] is False and ex["motivo"] == "docker_ausente"
    assert "Docker" in ex["mensagem"] and "Instale o Docker" in ex["instrucao"]
    corpo = {"name": "B", "inputs": [], "outputs": [{"id": "x", "label": "X", "type": "numero"}], "params": [],
             "code": 'def run(inputs, params):\n    return {"x": 1}\n'}
    salvo = c.post("/api/blocos", json=corpo)  # editar/salvar continua possível, com aviso
    assert salvo.status_code == 201 and "executor isolado está indisponível" in salvo.json()["warnings"][0]
    b = salvo.json()["block"]
    # testar e executar NÃO caem em execução sem isolamento
    teste = c.post("/api/blocos/testar", json={"ref": {"type": b["id"], "version": 1}}).json()
    assert teste["state"] == "falhou" and teste["error"]["code"] == "executor_indisponivel"
    assert teste["steps"][0]["outputs"] is None
    f = fluxo([bloco("p", b["id"], versao=1), saida("o")], [con("c", "p", "x", "o", "valor")])
    p = c.post("/api/projetos", json={"name": "x", "flow": f}).json()
    r = c.post(f"/api/projetos/{p['id']}/execucoes", json={})
    assert r.status_code == 422
    assert [i["code"] for i in r.json()["error"]["issues"]] == ["executor_indisponivel"]
    v = c.post("/api/fluxos/validar", json={"flow": f}).json()
    assert v["valid"] is False and any(i["code"] == "executor_indisponivel" for i in v["issues"])
    # blocos internos continuam funcionando
    soma = fluxo([constante("a", "numero", 1), saida("o")], [con("c", "a", "valor", "o", "valor")])
    p2 = c.post("/api/projetos", json={"name": "y", "flow": soma}).json()
    assert aguardar(c, c.post(f"/api/projetos/{p2['id']}/execucoes", json={}).json()["id"])["state"] == "concluido"


# ------------------------------------------------------------------------------ exemplos entregues
def test_exemplos_sao_criados_na_primeira_execucao_e_produzem_os_resultados_documentados(settings, executor):
    with cliente(dataclasses.replace(settings, semear_exemplos=True), executor) as c:
        projetos = {p["name"]: p for p in c.get("/api/projetos").json()}
        assert len(projetos) == 4
        resultados = {}
        for nome, p in projetos.items():
            run = aguardar(c, c.post(f"/api/projetos/{p['id']}/execucoes", json={}).json()["id"])
            assert run["state"] == "concluido", (nome, run["error"])
            resultados[nome.split(" — ")[0]] = (run["result"]["outputs"], etapas(run))
    assert resultados["Exemplo 1"][0][0]["value"] == "Olá, Ana!"
    assert resultados["Exemplo 2"][0][0]["value"] == 5
    saidas, e = resultados["Exemplo 3"]
    assert [(o["title"], o["value"]) for o in saidas] == [("Valor com desconto", 225)]
    assert e["saida_nao"]["state"] == "ignorado" and e["desconto"]["state"] == "concluido"
    assert resultados["Exemplo 4"][0][0]["value"] == [30, 60, 90]
    with cliente(dataclasses.replace(settings, semear_exemplos=True), executor) as c:  # não duplica ao reiniciar
        assert len(c.get("/api/projetos").json()) == 4
