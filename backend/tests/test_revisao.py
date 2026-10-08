"""Regressões dos achados da revisão independente (cada teste cita o problema que provou existir)."""

from __future__ import annotations

import threading
import time
import tracemalloc


from app.models import Connection, Flow
from app.validation import verificar_conexao

from .conftest import IMAGEM, LIMITES_TESTE, cliente
from .helpers import analisar_dict, bloco, con, constante, fluxo, resolver_builtin, saida
from app.sandbox import DockerExecutor


def _pico_de_memoria(funcao):
    """Executa `funcao` e devolve (resultado, pico de bytes alocados em Python)."""
    tracemalloc.start()
    try:
        resultado = funcao()
        return resultado, tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def _texto_pelo_bloco(sem_docker, texto, **params):
    f = fluxo([bloco("i", "builtin.inicio", {"dados": {"t": texto}}), bloco("s", "builtin.selecionar_campos", {"caminhos": "t"}),
               bloco("x", "builtin.texto", params), saida()],
              [con("c0", "i", "dados", "s", "objeto"), con("c1", "s", "valor", "x", "texto"),
               con("c2", "x", "resultado", "saida", "valor")])
    inicio = time.monotonic()
    run = sem_docker.executar(f)
    return run, time.monotonic() - inicio


# ---------------------------------------------------------------- achado 1: blocos internos amplificam dados
def test_substituir_que_multiplicaria_o_texto_e_recusado_antes_de_montar(sem_docker):
    (run, _), pico = _pico_de_memoria(lambda: _texto_pelo_bloco(
        sem_docker, "a" * 500_000, operacao="substituir", buscar="a", substituir_por="b" * 500))
    assert run["state"] == "falhou"
    erro = run["error"]
    assert erro["code"] == "valor_grande_demais" and "grande demais" in erro["message"]
    assert pico < 60 * 1024 * 1024, f"o bloco chegou a alocar {pico / 1e6:.0f} MB antes de recusar"  # sem a guarda: ~250 MB


def test_substituir_dentro_do_limite_continua_funcionando(sem_docker):
    run, _ = _texto_pelo_bloco(sem_docker, "a-b-c", operacao="substituir", buscar="-", substituir_por="+")
    assert run["result"]["outputs"][0]["value"] == "a+b+c"


def test_adicionar_texto_em_lista_confere_o_total_antes(sem_docker):
    f = fluxo([constante("l", "lista", ["x"] * 5000),
               bloco("c", "builtin.para_cada", {"operacao": "adicionar_texto", "prefixo": "p" * 5000, "limite": 10000}), saida()],
              [con("c1", "l", "valor", "c", "lista"), con("c2", "c", "resultado", "saida", "valor")])
    run, pico = _pico_de_memoria(lambda: sem_docker.executar(f))
    assert run["state"] == "falhou" and run["error"]["code"] == "valor_grande_demais"
    assert pico < 12 * 1024 * 1024, f"a nova lista (~25 MB) foi montada antes de ser recusada: pico {pico / 1e6:.0f} MB"


def test_selecionar_campos_limita_quantidade_e_amplificacao_por_alias(sem_docker):
    grande = ["x" * 1000] * 300  # ~300 KB
    def rodar(caminhos):
        f = fluxo([bloco("i", "builtin.inicio", {"dados": {"l": [grande]}}),
                   bloco("s", "builtin.selecionar_campos", {"caminhos": caminhos}), saida()],
                  [con("c1", "i", "dados", "s", "objeto"), con("c2", "s", "selecionados", "saida", "valor")])
        return sem_docker.executar(f)

    run = rodar("\n".join(f"l.{'0' * k}" for k in range(1, 6)))  # l.0, l.00, ... apontam para o MESMO trecho grande
    assert run["state"] == "falhou" and run["error"]["code"] == "valor_grande_demais"
    run = rodar("\n".join(f"campo{i}" for i in range(51)))
    assert run["state"] == "falhou" and "máximo é 50" in run["error"]["message"]


def test_parametros_de_texto_e_json_enormes_sao_rejeitados_na_validacao():
    a = analisar_dict(fluxo([bloco("t", "builtin.texto", {"operacao": "substituir", "buscar": "x" * 100_001})]))
    assert any(i.param == "buscar" and "grande demais" in i.message for i in a.issues)
    a = analisar_dict(fluxo([constante("c", "lista", ["x" * 1000] * 1100)]))  # ~1,1 MB digitado em um parâmetro
    assert any(i.param == "valor" and "grande demais" in i.message for i in a.issues)


# ---------------------------------------------------------------- achado 13: valores inesperados não viram 500
def test_parametro_de_tipo_nao_hashable_nao_derruba_a_validacao():
    for ruim in ([], {}, [1], {"a": 1}):
        a = analisar_dict(fluxo([bloco("c", "builtin.constante", {"tipo": ruim, "valor": 1})]))
        assert any(i.code == "parametro_invalido" for i in a.issues)  # vira problema de configuração, não exceção


# ---------------------------------------------------------------- achado 5: conexão que invalida outra
def test_conexao_que_invalida_outra_conexao_e_recusada_ao_conectar():
    # ligar um TEXTO à entrada de uma condição muda o tipo que ela repassa e quebra uma conexão que já existia
    g = Flow.model_validate(fluxo(
        [constante("t", "texto", "x"), bloco("c", "builtin.condicao", {"operador": "igual", "comparar_com": "a"}),
         constante("n", "numero", 1), bloco("m", "builtin.matematica")],
        [con("k1", "n", "valor", "m", "b")]))
    ok = Connection.model_validate(con("k2", "c", "verdadeiro", "m", "a"))
    # c.verdadeiro herda o tipo de c.valor (ainda sem ligação → qualquer): aceito agora...
    assert verificar_conexao(g, ok, resolver_builtin) == []
    g2 = g.model_copy(update={"connections": [*g.connections, ok]})
    # ...e ligar o texto depois quebra a conexão k2 (texto → número): precisa ser recusado AGORA
    texto = Connection.model_validate(con("k3", "t", "valor", "c", "valor"))
    problemas = verificar_conexao(g2, texto, resolver_builtin)
    assert [p.code for p in problemas] == ["tipo_incompativel"] and problemas[0].connection_id == "k2"


# ---------------------------------------------------------------- achados 4 e 13 pela API
def _projeto(c, nome="p"):
    return c.post("/api/projetos", json={"name": nome}).json()


def _aninhado(n):
    v = {"x": 1}
    for _ in range(n):
        v = {"a": v}
    return v


def test_params_muito_aninhados_sao_recusados_e_o_projeto_continua_abrivel(client_sem_docker):
    c = client_sem_docker
    p = _projeto(c)
    bom = fluxo([bloco("i", "builtin.inicio", {"dados": {"a": 1}})])
    assert c.put(f"/api/projetos/{p['id']}", json={"flow": bom}).status_code == 200
    profundo = fluxo([bloco("i", "builtin.inicio", {"dados": _aninhado(300)})])
    r = c.put(f"/api/projetos/{p['id']}", json={"flow": profundo})
    assert r.status_code == 422 and "aninhada" in str(r.json())
    assert c.get(f"/api/projetos/{p['id']}").json()["flow"] == bom  # o último fluxo válido permanece e abre normalmente
    assert c.post("/api/projetos", json={"name": "novo", "flow": profundo}).status_code == 422
    assert len(c.get("/api/projetos").json()) == 1  # e nada foi criado


def test_surrogate_solitario_e_recusado_em_vez_de_virar_erro_500(client_sem_docker):
    c = client_sem_docker
    p = _projeto(c)
    ruim = '{"flow": {"blocks": [{"id": "i", "type": "builtin.inicio", "version": 1, "position": {"x": 0, "y": 0}, "params": {"dados": {"a": "\\ud83d"}}, "label": null}], "connections": []}}'
    r = c.put(f"/api/projetos/{p['id']}", content=ruim, headers={"content-type": "application/json"})
    assert r.status_code == 422
    r = c.post(f"/api/projetos/{p['id']}/execucoes", content='{"initial_data": {"i": {"a": "\\ud83d"}}}', headers={"content-type": "application/json"})
    assert r.status_code == 422
    r = c.post("/api/blocos/testar", content='{"ref": {"type": "builtin.matematica", "version": 1}, "inputs": {"a": "\\ud83d"}}',
               headers={"content-type": "application/json"})
    assert r.status_code == 422


def test_versoes_fora_da_faixa_viram_422_ou_404_nunca_500(client_sem_docker):
    c = client_sem_docker
    assert c.get(f"/api/blocos/builtin.matematica/versoes/{10**30}").status_code == 422
    assert c.get(f"/api/blocos/builtin.matematica/exemplo?version={10**30}").status_code == 422
    assert c.get("/api/blocos/builtin.matematica/versoes/0").status_code == 422
    f = fluxo([bloco("m", "builtin.matematica", versao=10**30)])
    assert c.post("/api/fluxos/validar", json={"flow": f}).status_code == 422
    assert c.post("/api/blocos/testar", json={"ref": {"type": "builtin.matematica", "version": 10**30}}).status_code in (404, 422)


def test_tipo_nao_hashable_na_api_nao_e_500(client_sem_docker):
    c = client_sem_docker
    f = fluxo([bloco("c", "builtin.constante", {"tipo": [], "valor": 1})])
    r = c.post("/api/fluxos/validar", json={"flow": f})
    assert r.status_code == 200 and not r.json()["valid"]
    assert c.post("/api/projetos", json={"name": "x", "flow": f}).status_code == 201  # rascunho salvável


def test_teste_de_bloco_com_projeto_inexistente_e_404(client_sem_docker):
    r = client_sem_docker.post("/api/blocos/testar", json={"ref": {"type": "builtin.matematica", "version": 1}, "project_id": "prj_inexistente",
                                                           "inputs": {"a": 1, "b": 2}, "params": {"operacao": "somar"}})
    assert r.status_code == 404


def test_edicoes_simultaneas_do_mesmo_bloco_geram_versoes_distintas(client_sem_docker):
    c = client_sem_docker
    corpo = {"name": "Concorrente", "inputs": [], "outputs": [{"id": "x", "label": "X", "type": "numero"}], "params": [],
             "code": 'def run(inputs, params):\n    return {"x": 1}\n'}
    bid = c.post("/api/blocos", json=corpo).json()["block"]["id"]
    resultados: list[tuple[int, int]] = []

    def editar(n):
        r = c.put(f"/api/blocos/{bid}", json={**corpo, "description": f"edição {n}"})
        resultados.append((r.status_code, r.json().get("block", {}).get("version", 0)))

    ts = [threading.Thread(target=editar, args=(n,)) for n in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(v for s, v in resultados if s == 201) == [2, 3, 4, 5, 6, 7], resultados
    assert all(s == 201 for s, _ in resultados)


# ---------------------------------------------------------------- achado 14: importação
def _envelope(blocos_custom, nome="x"):
    return {"format": "trama.fluxo", "format_version": 1, "project": {"name": nome},
            "flow": {"schema_version": 1, "blocks": [], "connections": [], "viewport": None}, "custom_blocks": blocos_custom}


def _custom(i, code='def run(inputs, params):\n    return {"x": 1}\n', versao=1):
    return {"id": f"custom.b{i}", "version": versao, "name": f"B{i}", "description": "", "category": "Personalizados", "kind": "python",
            "icon": None, "inputs": [], "outputs": [{"id": "x", "label": "X", "type": "numero", "required": True, "description": "",
                                                    "type_from": None, "conditional": False}], "params": [], "code": code}


def test_importacao_limita_e_valida_o_que_o_arquivo_traz(client_sem_docker):
    c = client_sem_docker
    assert c.post("/api/projetos/importar", json=_envelope([_custom(i) for i in range(60)])).status_code == 422   # > 50
    assert c.post("/api/projetos/importar", json=_envelope([_custom(1), _custom(1)])).status_code == 422          # repetido
    assert c.post("/api/projetos/importar", json=_envelope([], nome="")).status_code == 422                       # nome vazio
    assert c.post("/api/projetos/importar/validar", json=_envelope([_custom(1), _custom(1)])).status_code == 422  # validar == importar
    r = c.post("/api/projetos/importar", json=_envelope([_custom(1), _custom(2)]))
    assert r.status_code == 201 and r.json()["created_blocks"] == []                       # nenhum é usado pelo fluxo
    assert any("ignorados" in w for w in r.json()["warnings"])
    assert [b for b in c.get("/api/blocos").json() if b["id"].startswith("custom.")] == []  # a biblioteca não foi poluída


# ---------------------------------------------------------------- achado 12: proteção do serviço local
def test_host_ipv6_chunked_e_cabecalhos_de_seguranca(client_sem_docker):
    c = client_sem_docker
    assert c.get("/api/sistema", headers={"host": "[::1]:8000"}).status_code == 200
    assert c.get("/api/sistema", headers={"host": "localhost:5173"}).status_code == 200
    assert c.get("/api/sistema", headers={"host": "evil.example"}).status_code == 400
    assert c.get("/api/sistema", headers={"host": "localhost.evil.example"}).status_code == 400

    def corpo():
        yield b'{"name": "x"}'

    r = c.post("/api/projetos", content=corpo(), headers={"content-type": "application/json"})  # chunked, sem Content-Length
    assert r.status_code == 411
    r = c.get("/api/sistema")
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"


def test_frontend_servido_tem_csp_e_caminho_com_nul_nao_e_500(settings, tmp_path):
    import dataclasses
    pasta = tmp_path / "dist"
    (pasta / "assets").mkdir(parents=True)
    (pasta / "index.html").write_text("<!doctype html><title>x</title>", encoding="utf-8")
    ex = DockerExecutor(IMAGEM, LIMITES_TESTE, docker_bin="docker-que-nao-existe-xyz")
    with cliente(dataclasses.replace(settings, pasta_frontend=pasta), ex) as c:
        r = c.get("/")
        assert r.status_code == 200 and "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert "script-src 'self'" in r.headers["content-security-policy"]
        assert c.get("/%00").status_code == 200  # cai no index.html, em vez de 500
        assert c.get("/../../etc/passwd").text.startswith("<!doctype html>")


def test_inteiros_grandes_sao_preservados_pelo_backend(sem_docker):
    grande = 9007199254740993  # 2**53 + 1: o JavaScript do navegador não consegue representá-lo, o Python sim
    f = fluxo([constante("a", "numero", grande), constante("b", "numero", 1), bloco("m", "builtin.matematica", {"operacao": "somar"}), saida()],
              [con("c1", "a", "valor", "m", "a"), con("c2", "b", "valor", "m", "b"), con("c3", "m", "resultado", "saida", "valor")])
    assert sem_docker.executar(f)["result"]["outputs"][0]["value"] == grande + 1
