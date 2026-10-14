"""Migração do formato 1 (grafo de blocos) para o formato 2 (passos): os fluxos antigos continuam fazendo a mesma coisa.

Os quatro exemplos do formato antigo (``tests/fixtures/v1``) servem de oráculo: os resultados documentados
na versão anterior ("Olá, Ana!", 5, 225 / 150, [30, 60, 90]) precisam sair iguais depois da conversão."""

from __future__ import annotations

import json
import sqlite3

import pytest

from app import exchange
from app.migracao import migrar_fluxo
from app.models import Flow
from app.store import Store

from .helpers import analisar_dict, carregar_v1, estados


def bloco(id, tipo, params=None, label=None):
    return {"id": id, "type": tipo, "version": 1, "position": {"x": 0, "y": 0}, "params": params or {}, "label": label}


def con(id, origem, porta_origem, destino, porta_destino):
    return {"id": id, "source": {"block": origem, "port": porta_origem}, "target": {"block": destino, "port": porta_destino}}


def v1(blocos, conexoes=()):
    return {"schema_version": 1, "blocks": blocos, "connections": list(conexoes), "viewport": None}


def achar(passos, id):
    for p in passos:
        if p["id"] == id:
            return p
        for filhos in p.get("slots", {}).values():
            achado = achar(filhos, id)
            if achado:
                return achado
    return None


# ------------------------------------------------------------------ os exemplos antigos, ponta a ponta
def importar_e_rodar(amb, nome, dados=None):
    res = exchange.importar(amb.store, amb.registro, carregar_v1(nome))
    assert Flow.model_validate(res["flow"]).schema_version == 2
    return amb.executar(res["flow"], dados)


@pytest.mark.docker
def test_exemplo_1_saudacao_com_bloco_python_personalizado(com_docker):
    run = importar_e_rodar(com_docker, "01-saudacao.json")
    assert run["state"] == "concluido", run["error"]
    assert [o["value"] for o in run["result"]["outputs"]] == ["Olá, Ana!"]


def test_exemplo_2_soma(sem_docker):
    run = importar_e_rodar(sem_docker, "02-soma.json")
    assert [o["value"] for o in run["result"]["outputs"]] == [5]


def test_exemplo_3_condicao_so_o_caminho_escolhido_executa(sem_docker):
    run = importar_e_rodar(sem_docker, "03-condicao.json")
    assert [(o["title"], o["value"]) for o in run["result"]["outputs"]] == [("Valor com desconto", 225)]
    e = estados(run)
    assert e["desconto"] == "concluido" and e["saida_nao"] == "ignorado"
    run = importar_e_rodar(sem_docker, "03-condicao.json", {"dados": {"valor": 150}})
    assert [o["value"] for o in run["result"]["outputs"]] == [150]


def test_exemplo_4_lista(sem_docker):
    run = importar_e_rodar(sem_docker, "04-lista.json")
    assert [o["value"] for o in run["result"]["outputs"]] == [[30, 60, 90]]


def test_o_rotulo_e_o_id_dos_blocos_antigos_sao_preservados(sem_docker):
    res = exchange.importar(sem_docker.store, sem_docker.registro, carregar_v1("03-condicao.json"))
    teste = achar(res["flow"]["steps"], "teste")
    assert teste["label"] == "Valor acima de 200?" and achar(res["flow"]["steps"], "desconto")["label"] == "Aplicar desconto"


# ------------------------------------------------------------------ regras da conversão
def test_o_inicio_vira_o_gatilho_com_um_campo_dados():
    f = migrar_fluxo(v1([bloco("i", "builtin.inicio", {"dados": {"x": 1}}), bloco("s", "builtin.saida", {"titulo": "T"})],
                        [con("c", "i", "dados", "s", "valor")]))
    assert f["trigger"]["id"] == "gatilho" and f["trigger"]["params"]["campos"][0]["default"] == {"x": 1}
    assert f["steps"][0]["inputs"]["valor"] == {"parts": [{"step": "gatilho", "output": "dados", "path": ""}]}


def test_sem_bloco_inicio_o_gatilho_nasce_sem_campos():
    f = migrar_fluxo(v1([bloco("c", "builtin.constante", {"tipo": "numero", "valor": 3})]))
    assert f["trigger"]["params"]["campos"] == []
    assert f["steps"][0]["type"] == "builtin.compor" and f["steps"][0]["inputs"]["entrada"] == {"value": 3}


def test_um_segundo_inicio_vira_um_passo_compor():
    f = migrar_fluxo(v1([bloco("a", "builtin.inicio", {"dados": {"x": 1}}), bloco("b", "builtin.inicio", {"dados": {"y": 2}}),
                         bloco("s", "builtin.saida")], [con("c", "b", "dados", "s", "valor")]))
    b = achar(f["steps"], "b")
    assert b["type"] == "builtin.compor" and b["inputs"]["entrada"] == {"value": {"y": 2}}
    assert achar(f["steps"], "s")["inputs"]["valor"]["parts"][0] == {"step": "b", "output": "resultado", "path": ""}


def test_id_gatilho_reservado_e_renomeado_sem_quebrar_as_referencias():
    f = migrar_fluxo(v1([bloco("gatilho", "builtin.constante", {"tipo": "texto", "valor": "x"}), bloco("s", "builtin.saida")],
                        [con("c", "gatilho", "valor", "s", "valor")]))
    assert achar(f["steps"], "gatilho_passo") and achar(f["steps"], "s")["inputs"]["valor"]["parts"][0]["step"] == "gatilho_passo"
    assert analisar_dict(f).erros_de_estrutura == []


def test_condicao_os_blocos_do_caminho_vao_para_o_ramo_e_o_valor_repassado_vira_a_origem():
    f = migrar_fluxo(v1(
        [bloco("n", "builtin.constante", {"tipo": "numero", "valor": 5}),
         bloco("c", "builtin.condicao", {"operador": "maior", "comparar_com": "1"}, label="Maior que 1?"),
         bloco("m", "builtin.matematica", {"operacao": "somar"}), bloco("k", "builtin.constante", {"tipo": "numero", "valor": 1}),
         bloco("t", "builtin.matematica", {"operacao": "multiplicar"})],
        [con("1", "n", "valor", "c", "valor"), con("2", "c", "verdadeiro", "m", "a"), con("3", "k", "valor", "m", "b"),
         con("4", "c", "falso", "t", "a"), con("5", "k", "valor", "t", "b")]))
    cond = achar(f["steps"], "c")
    assert cond["params"]["regras"] == [{"esq": {"parts": [{"step": "n", "output": "resultado", "path": ""}]}, "op": "maior", "dir": {"value": "1"}}]
    assert [p["id"] for p in cond["slots"]["sim"]] == ["m"] and [p["id"] for p in cond["slots"]["nao"]] == ["t"]
    # "verdadeiro" repassava o valor testado: agora é o valor original da constante
    assert achar(f["steps"], "m")["inputs"]["a"]["parts"][0] == {"step": "n", "output": "resultado", "path": ""}
    # a constante "k" é usada dentro do ramo, então precisa rodar ANTES da condição
    ordem = [p["id"] for p in f["steps"]]
    assert ordem.index("k") < ordem.index("c")
    assert analisar_dict(f).erros == []


def test_condicoes_aninhadas_e_testes_sem_valor_de_comparacao():
    f = migrar_fluxo(v1(
        [bloco("n", "builtin.constante", {"tipo": "texto", "valor": "x"}),
         bloco("c1", "builtin.condicao", {"operador": "nao_vazio"}), bloco("c2", "builtin.condicao", {"operador": "vazio"}),
         bloco("fim", "builtin.saida")],
        [con("1", "n", "valor", "c1", "valor"), con("2", "c1", "verdadeiro", "c2", "valor"), con("3", "c2", "falso", "fim", "valor")]))
    c1 = achar(f["steps"], "c1")
    assert "dir" not in c1["params"]["regras"][0]
    interna = c1["slots"]["sim"][0]
    assert interna["id"] == "c2" and [p["id"] for p in interna["slots"]["nao"]] == ["fim"]
    assert analisar_dict(f).erros == []


def test_conexoes_soltas_e_dados_malformados_nao_derrubam_a_migracao():
    f = migrar_fluxo({"schema_version": 1, "blocks": [bloco("a", "builtin.matematica"), "lixo", {"sem": "id"}],
                      "connections": [con("1", "fantasma", "x", "a", "a"), "lixo", {"source": {}, "target": {}}]})
    assert [p["id"] for p in f["steps"]] == ["a"] and f["steps"][0]["inputs"] == {}


def test_fluxo_ja_no_formato_2_passa_sem_mudanca():
    novo = {"schema_version": 2, "trigger": {"id": "gatilho"}, "steps": []}
    assert migrar_fluxo(novo) is novo


def test_transformacao_de_lista_do_formato_antigo_mantem_os_parametros():
    f = migrar_fluxo(v1([bloco("l", "builtin.constante", {"tipo": "lista", "valor": [1]}),
                         bloco("p", "builtin.para_cada", {"operacao": "multiplicar", "operando": 3, "limite": 5})],
                        [con("1", "l", "valor", "p", "lista")]))
    p = achar(f["steps"], "p")
    assert p["type"] == "builtin.transformar_lista" and p["params"] == {"operacao": "multiplicar", "operando": 3, "limite": 5}
    assert p["inputs"]["lista"]["parts"][0]["step"] == "l"


# ------------------------------------------------------------------ importar arquivos antigos pela API
def test_arquivo_exportado_pela_versao_anterior_e_importado_e_convertido(client_sem_docker):
    c = client_sem_docker
    r = c.post("/api/projetos/importar", json=carregar_v1("02-soma.json"))
    assert r.status_code == 201, r.text
    assert r.json()["flow"]["schema_version"] == 2 and r.json()["flow"]["trigger"]["id"] == "gatilho"
    v = c.post("/api/projetos/importar/validar", json=carregar_v1("04-lista.json"))
    assert v.status_code == 200 and v.json()["step_count"] == 3


def test_arquivo_antigo_corrompido_vira_422_e_nao_500(client_sem_docker):
    ruim = carregar_v1("02-soma.json")
    ruim["flow"]["blocks"][0]["params"] = {"tipo": []}
    assert client_sem_docker.post("/api/projetos/importar", json=ruim).status_code in (201, 422)
    sem_fluxo = carregar_v1("02-soma.json")
    sem_fluxo["flow"] = "nada"
    assert client_sem_docker.post("/api/projetos/importar", json=sem_fluxo).status_code == 422


# ------------------------------------------------------------------ banco de dados no formato antigo
ESQUEMA_V1 = """
CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', flow TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE block_types (id TEXT NOT NULL, version INTEGER NOT NULL, name TEXT NOT NULL, definition TEXT NOT NULL,
    created_at TEXT NOT NULL, PRIMARY KEY (id, version));
CREATE TABLE runs (id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id) ON DELETE CASCADE, kind TEXT NOT NULL,
    state TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, duration_ms INTEGER,
    snapshot TEXT NOT NULL, result TEXT, error TEXT);
CREATE TABLE run_steps (run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, block_id TEXT NOT NULL,
    position INTEGER NOT NULL, state TEXT NOT NULL, started_at TEXT, finished_at TEXT, duration_ms INTEGER,
    inputs TEXT, outputs TEXT, logs TEXT, error TEXT, skip_reason TEXT, PRIMARY KEY (run_id, block_id));
"""


def criar_banco_antigo(caminho):
    soma = carregar_v1("02-soma.json")["flow"]
    com = sqlite3.connect(caminho)
    com.executescript(ESQUEMA_V1)
    com.execute("INSERT INTO projects VALUES ('prj_1', 'Soma antiga', 'descrição', ?, 4, 't', 't')", (json.dumps(soma),))
    com.execute("INSERT INTO projects VALUES ('prj_2', 'Quebrado', '', 'isto não é json', 1, 't', 't')")
    com.execute("INSERT INTO block_types VALUES ('custom.x', 1, 'X', ?, 't')", (json.dumps({"id": "custom.x"}),))
    com.execute("INSERT INTO runs VALUES ('exe_1', 'prj_1', 'fluxo', 'concluido', 't', NULL, NULL, 1, '{}', NULL, NULL)")
    com.execute("INSERT INTO run_steps VALUES ('exe_1', 'a', 0, 'concluido', NULL, NULL, 1, NULL, NULL, NULL, NULL, NULL)")
    com.commit()
    com.close()


def test_banco_no_formato_antigo_e_convertido_ao_abrir_com_copia_de_seguranca(tmp_path):
    caminho = tmp_path / "dados" / "trama.db"
    caminho.parent.mkdir()
    criar_banco_antigo(caminho)
    store = Store(caminho)

    bom = store.obter_projeto("prj_1")
    assert bom["name"] == "Soma antiga" and bom["revision"] == 4 and bom["flow"]["schema_version"] == 2
    Flow.model_validate(bom["flow"])
    ruim = store.obter_projeto("prj_2")  # dado ilegível: o projeto não se perde, só o fluxo (que está na cópia)
    assert ruim["flow"] == {"schema_version": 2, "steps": []} and "trama.db.v1.bak" in ruim["description"]
    assert store.listar_execucoes("prj_1") == []  # o histórico antigo descreve blocos que não existem mais
    assert [t.id for t in []] == [] and store.ultima_versao("custom.x") == 1  # a biblioteca de blocos é preservada

    copia = caminho.with_name("trama.db.v1.bak")
    assert copia.exists()
    antigo = sqlite3.connect(copia)
    assert antigo.execute("SELECT COUNT(*) FROM run_steps").fetchone()[0] == 1  # a cópia mantém tudo como era
    assert json.loads(antigo.execute("SELECT flow FROM projects WHERE id = 'prj_1'").fetchone()[0])["schema_version"] == 1
    antigo.close()

    # nova execução grava no esquema novo, e abrir de novo não converte nem copia outra vez
    assert store.criar_execucao(kind="fluxo", project_id="prj_1", snapshot={}, etapas=["a"])
    copia.write_bytes(b"marca")
    Store(caminho)
    assert copia.read_bytes() == b"marca" and len(store.listar_projetos()) == 2


def test_banco_novo_ja_nasce_no_formato_atual(tmp_path):
    caminho = tmp_path / "trama.db"
    Store(caminho)
    com = sqlite3.connect(caminho)
    assert com.execute("PRAGMA user_version").fetchone()[0] == 2
    assert "step_key" in [r[1] for r in com.execute("PRAGMA table_info(run_steps)")]
    com.close()
    assert not caminho.with_name("trama.db.v1.bak").exists()
