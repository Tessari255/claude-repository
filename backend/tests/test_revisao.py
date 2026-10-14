"""Regressões dos achados da revisão independente de segurança (cada teste cita o problema que provou existir),
mais as proteções equivalentes para o formato de passos (aninhamento, conteúdo dinâmico, repetições)."""

from __future__ import annotations

import threading
import time
import tracemalloc

from app.sandbox import DockerExecutor

from .conftest import IMAGEM, LIMITES_TESTE, cliente
from .helpers import analisar_dict, campo, compor, etapas, fluxo, lit, matematica, passo, ref, saida, tpl


def _pico_de_memoria(funcao):
    """Executa `funcao` e devolve (resultado, pico de bytes alocados em Python)."""
    tracemalloc.start()
    try:
        resultado = funcao()
        return resultado, tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def _texto_pelo_passo(sem_docker, texto, **params):
    f = fluxo([passo("x", "builtin.texto", {"texto": ref("gatilho", "t")}, params), saida("s", "r", ref("x", "resultado"))], [campo("t", "texto", texto)])
    return sem_docker.executar(f)


# ---------------------------------------------------------------- achado 1: blocos internos amplificam dados
def test_substituir_que_multiplicaria_o_texto_e_recusado_antes_de_montar(sem_docker):
    run, pico = _pico_de_memoria(lambda: _texto_pelo_passo(sem_docker, "a" * 500_000, operacao="substituir", buscar="a", substituir_por="b" * 500))
    assert run["state"] == "falhou"
    assert run["error"]["code"] == "valor_grande_demais" and "grande demais" in run["error"]["message"]
    assert pico < 60 * 1024 * 1024, f"o passo chegou a alocar {pico / 1e6:.0f} MB antes de recusar"


def test_substituir_dentro_do_limite_continua_funcionando(sem_docker):
    run = _texto_pelo_passo(sem_docker, "a-b-c", operacao="substituir", buscar="-", substituir_por="+")
    assert run["result"]["outputs"][0]["value"] == "a+b+c"


def test_adicionar_texto_em_lista_confere_o_total_antes(sem_docker):
    f = fluxo([passo("c", "builtin.transformar_lista", {"lista": lit(["x"] * 5000)}, {"operacao": "adicionar_texto", "prefixo": "p" * 5000, "limite": 10000}),
               saida("s", "r", ref("c", "resultado"))])
    run, pico = _pico_de_memoria(lambda: sem_docker.executar(f))
    assert run["state"] == "falhou" and run["error"]["code"] == "valor_grande_demais"
    assert pico < 12 * 1024 * 1024, f"a nova lista (~25 MB) foi montada antes de ser recusada: pico {pico / 1e6:.0f} MB"


def test_selecionar_campos_limita_quantidade_e_amplificacao_por_alias(sem_docker):
    grande = ["x" * 1000] * 300  # ~300 KB

    def rodar(caminhos):
        f = fluxo([passo("s", "builtin.selecionar_campos", {"objeto": ref("gatilho", "d")}, {"caminhos": caminhos}),
                   saida("o", "r", ref("s", "selecionados"))], [campo("d", "json", {"l": [grande]})])
        return sem_docker.executar(f)

    run = rodar("\n".join(f"l.{'0' * k}" for k in range(1, 6)))  # l.0, l.00, ... apontam para o MESMO trecho grande
    assert run["state"] == "falhou" and run["error"]["code"] == "valor_grande_demais"
    run = rodar("\n".join(f"campo{i}" for i in range(51)))
    assert run["state"] == "falhou" and "máximo é 50" in run["error"]["message"]


def test_parametros_de_texto_e_json_enormes_sao_rejeitados_na_validacao():
    a = analisar_dict(fluxo([passo("t", "builtin.texto", {"texto": lit("a")}, {"operacao": "substituir", "buscar": "x" * 100_001})]))
    assert any(i.field == "buscar" and "grande demais" in i.message for i in a.issues)
    a = analisar_dict(fluxo([passo("p", "builtin.transformar_lista", {"lista": lit([1])},
                                   {"operacao": "extrair_campo", "campo": "x" * 100_001, "limite": 5})]))
    assert any(i.field == "campo" and "grande demais" in i.message for i in a.issues)


# ---------------------------------------------------------------- achado 13: valores inesperados não viram 500
def test_parametro_de_tipo_nao_hashable_nao_derruba_a_validacao():
    for ruim in ([], {}, [1], {"a": 1}):
        a = analisar_dict(fluxo([passo("v", "builtin.var_inicializar", {}, {"nome": "x", "tipo": ruim})]))
        assert any(i.code == "parametro_invalido" for i in a.issues)  # vira problema de configuração, não exceção
        b = analisar_dict(fluxo([passo("c", "builtin.condicao", params={"regras": ruim, "combinador": ruim}, slots={"sim": [], "nao": []})]))
        assert any(i.code == "parametro_invalido" for i in b.issues)
        c = analisar_dict(fluxo([passo("t", "builtin.texto", {"texto": lit("a")}, {"operacao": ruim})]))
        assert any(i.code == "parametro_invalido" for i in c.issues)


def test_declaracao_de_portas_malformada_vira_problema_de_configuracao():
    for ruim in ("texto", 5, [1], [{"id": "A B"}], [{"id": "a", "label": "x", "type": "tipo-que-nao-existe"}], [{"id": f"p{i}", "label": "x"} for i in range(30)]):
        p = passo("p", "builtin.python", params={"entradas": ruim, "saidas": [{"id": "r", "label": "R", "type": "texto"}], "codigo": "x"})
        a = analisar_dict(fluxo([p]))
        assert any(i.field == "entradas" for i in a.issues), ruim


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
    bom = fluxo([compor("c", lit({"a": 1}))])
    assert c.put(f"/api/projetos/{p['id']}", json={"flow": bom}).status_code == 200
    for profundo in (fluxo([compor("c", lit(_aninhado(300)))]), fluxo([passo("c", "builtin.compor", {}, {"x": _aninhado(300)})]),
                     fluxo([compor("c", {"parts": [], "value": 1})])):
        r = c.put(f"/api/projetos/{p['id']}", json={"flow": profundo})
        assert r.status_code == 422, r.text
        assert c.post("/api/projetos", json={"name": "novo", "flow": profundo}).status_code == 422
    assert "aninhada" in str(c.put(f"/api/projetos/{p['id']}", json={"flow": fluxo([compor("c", lit(_aninhado(300)))])}).json())
    assert c.get(f"/api/projetos/{p['id']}").json()["flow"]["steps"][0]["inputs"]["entrada"] == {"value": {"a": 1}}  # o último válido permanece
    assert len(c.get("/api/projetos").json()) == 1


def test_passos_aninhados_demais_sao_recusados_sem_derrubar_o_servidor(client_sem_docker):
    c = client_sem_docker
    p = _projeto(c)
    f = fluxo([compor("folha", lit(1))])
    for i in range(500):  # um JSON com 500 níveis de escopos
        f["steps"] = [passo(f"e{i}", "builtin.escopo", slots={"corpo": f["steps"]})]
    r = c.put(f"/api/projetos/{p['id']}", json={"flow": f})
    assert r.status_code == 422
    assert c.get("/api/sistema").status_code == 200  # o servidor segue de pé


def test_surrogate_solitario_e_recusado_em_vez_de_virar_erro_500(client_sem_docker):
    c = client_sem_docker
    p = _projeto(c)
    ruim = ('{"flow": {"schema_version": 2, "steps": [{"id": "a", "type": "builtin.compor", "version": 1, '
            '"inputs": {"entrada": {"value": "\\ud83d"}}}]}}')
    assert c.put(f"/api/projetos/{p['id']}", content=ruim, headers={"content-type": "application/json"}).status_code == 422
    r = c.post(f"/api/projetos/{p['id']}/execucoes", content='{"trigger_inputs": {"a": "\\ud83d"}}', headers={"content-type": "application/json"})
    assert r.status_code == 422
    r = c.post("/api/blocos/testar", content='{"ref": {"type": "builtin.matematica", "version": 1}, "inputs": {"a": "\\ud83d"}}',
               headers={"content-type": "application/json"})
    assert r.status_code == 422


def test_versoes_fora_da_faixa_viram_422_ou_404_nunca_500(client_sem_docker):
    c = client_sem_docker
    assert c.get(f"/api/blocos/builtin.matematica/versoes/{10**30}").status_code == 422
    assert c.get(f"/api/blocos/builtin.matematica/exemplo?version={10**30}").status_code == 422
    assert c.get("/api/blocos/builtin.matematica/versoes/0").status_code == 422
    f = fluxo([passo("m", "builtin.matematica", versao=10**30)])
    assert c.post("/api/fluxos/validar", json={"flow": f}).status_code == 422
    assert c.post("/api/blocos/testar", json={"ref": {"type": "builtin.matematica", "version": 10**30}}).status_code in (404, 422)


def test_tipo_nao_hashable_na_api_nao_e_500(client_sem_docker):
    c = client_sem_docker
    f = fluxo([passo("v", "builtin.var_inicializar", {}, {"nome": "x", "tipo": []})])
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
    return {"format": "trama.fluxo", "format_version": 2, "project": {"name": nome}, "flow": fluxo([]), "custom_blocks": blocos_custom}


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
    f = fluxo([matematica("m", lit(grande), lit(1)), saida("s", "r", ref("m", "resultado"))])
    assert sem_docker.executar(f)["result"]["outputs"][0]["value"] == grande + 1


# ---------------------------------------------------------------- proteções novas do formato de passos
def test_o_conteudo_dinamico_nunca_executa_nada_so_le_valores(sem_docker):
    # um texto parecido com código, com chaves ou com a sintaxe de referência, é só texto
    esquisito = "{{ gatilho.nome }} ${7*7} __import__('os').system('x') %(a)s {0.__class__}"
    f = fluxo([compor("c", tpl(esquisito, ("gatilho", "t"), esquisito)), saida("s", "r", ref("c", "resultado"))], [campo("t", "texto", esquisito)])
    assert sem_docker.executar(f)["result"]["outputs"][0]["value"] == esquisito * 3


def test_caminho_do_conteudo_dinamico_nao_alcanca_atributos_do_python(sem_docker):
    for caminho in ("__class__", "__class__.__mro__", "keys", "0"):
        f = fluxo([compor("c", ref("gatilho", "d", caminho))], [campo("d", "json", {"a": 1})])
        run = sem_docker.executar(f)
        assert run["state"] == "falhou" and run["error"]["code"] == "campo_ausente", caminho


def test_um_laco_enorme_com_corpo_grande_para_pelo_limite_de_registros_e_nao_enche_o_banco(sem_docker):
    corpo = [compor(f"c{i}", lit(i)) for i in range(20)]
    f = fluxo([passo("laco", "builtin.para_cada", {"lista": lit(list(range(1000)))}, {"limite": 1000}, slots={"corpo": corpo})])
    inicio = time.monotonic()
    run = sem_docker.executar(f)
    assert run["state"] == "falhou" and run["error"]["code"] == "registros_demais"
    assert len(run["steps"]) <= 5001 + 25 and time.monotonic() - inicio < 60


def test_execucao_cancelada_nao_deixa_passos_em_andamento(sem_docker):
    f = fluxo([matematica("quebra", lit(1), lit(0), "dividir", retry=5, intervalo=30), compor("depois", lit(1), run_after=["falhou"])])
    from app.models import Flow
    rid = sem_docker.motor.preparar(Flow.model_validate(f), None)
    t = threading.Thread(target=sem_docker.motor.rodar, args=(rid,))
    t.start()
    for _ in range(100):
        if etapas(sem_docker.store.obter_execucao(rid))["quebra"]["state"] == "executando" and sem_docker.motor.cancelar(rid):
            break
        time.sleep(0.05)
    t.join(10)
    assert not {s["state"] for s in sem_docker.store.obter_execucao(rid)["steps"]} & {"executando", "aguardando"}
