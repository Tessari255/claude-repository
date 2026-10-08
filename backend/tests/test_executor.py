"""Prova os limites e o isolamento do executor (critérios 2, 7 e 8 + requisitos de segurança)."""

from __future__ import annotations

import dataclasses
import subprocess

import pytest

from app.config import Limites
from app.sandbox import DockerExecutor, limpar_segredos

from .conftest import IMAGEM, LIMITES_TESTE

pytestmark = pytest.mark.docker

# O código abaixo contorna de propósito a lista de bibliotecas do runner (que é só
# conveniência) para provar que o que protege é o CONTÊINER, não o filtro de imports.
BYPASS = "real = __import__.__closure__[0].cell_contents\n"


def rodar(executor, codigo, entradas=None, params=None, **limites):
    lim = dataclasses.replace(LIMITES_TESTE, **limites) if limites else None
    return executor.run("block", codigo, entradas or {}, params or {}, limits=lim)


def test_execucao_real_devolve_saidas_e_logs(executor):
    r = rodar(executor, 'def run(inputs, params):\n    print("oi")\n    return {"mensagem": f"Olá, {inputs.get(\'nome\', \'mundo\')}!"}',
              {"nome": "Ana"})
    assert r.ok, r.error
    assert r.payload == {"outputs": {"mensagem": "Olá, Ana!"}}
    assert r.logs == [{"source": "stdout", "text": "oi\n"}]


def test_excecao_informa_tipo_e_linha(executor):
    r = rodar(executor, "def run(inputs, params):\n    x = 1\n    return {'a': x / 0}")
    assert not r.ok
    assert r.error["type"] == "ZeroDivisionError"
    assert r.error["line"] == 3
    assert r.error["snippet"] == "return {'a': x / 0}"
    assert "<bloco>" in r.error["traceback"]


def test_erro_de_sintaxe_informa_linha(executor):
    r = rodar(executor, "def run(inputs, params)\n    return {}")
    assert not r.ok and r.error["category"] == "sintaxe" and r.error["line"] == 1


def test_laco_infinito_e_encerrado_pelo_limite_de_tempo(executor):
    r = rodar(executor, 'def run(inputs, params):\n    print("antes")\n    while True:\n        pass', tempo_s=2)
    assert not r.ok
    assert r.error["category"] == "tempo_esgotado"
    assert r.error["line"] == 3  # aponta o laço
    assert r.logs and r.logs[0]["text"] == "antes\n"  # logs preservados
    assert r.duration_ms < 6000


def test_codigo_que_engole_o_limite_brando_e_abatido_pelo_host(executor):
    codigo = (
        "def run(inputs, params):\n"
        "    while True:\n"
        "        try:\n"
        "            while True: pass\n"
        "        except BaseException:\n"
        "            pass\n"
    )
    r = rodar(executor, codigo, tempo_s=1, folga_inicio_s=3)
    assert not r.ok and r.error["category"] == "tempo_esgotado"
    # o contêiner não pode ter ficado vivo
    vivos = subprocess.run(["docker", "ps", "-q", "--filter", "label=trama.executor=1"],
                           capture_output=True, text=True).stdout.split()
    assert vivos == []


def test_limite_de_memoria_com_memory_error(executor):
    r = rodar(executor, "def run(inputs, params):\n    x = bytearray(400 * 1024 * 1024)\n    return {}", memoria_mb=64)
    assert not r.ok and r.error["category"] == "memoria_excedida"
    assert r.error["line"] == 2


def test_limite_de_memoria_pelo_cgroup_mesmo_sem_rlimit(executor):
    codigo = ("def run(inputs, params):\n    x = bytearray(600 * 1024 * 1024)\n"
              "    for i in range(0, len(x), 4096):\n        x[i] = 1\n    return {}")
    r = rodar(executor, codigo, memoria_mb=64, rlimit_as=False, tempo_s=8)
    assert not r.ok and r.error["category"] == "memoria_excedida"


def test_volume_de_saida_via_print_e_limitado(executor):
    r = rodar(executor, 'def run(inputs, params):\n    while True:\n        print("x" * 1000)', logs_max=20_000)
    assert not r.ok and r.error["category"] == "saida_excessiva"
    assert sum(len(p["text"]) for p in r.logs) <= 20_000


def test_volume_de_saida_direto_no_descritor_e_abatido_pelo_host(executor):
    codigo = ("def run(inputs, params):\n    f = open(1, 'w', closefd=False)\n"
              "    while True:\n        f.write('x' * 65536)\n        f.flush()")
    r = rodar(executor, codigo, valor_max=64 * 1024, logs_max=16 * 1024, tempo_s=8)
    assert not r.ok and r.error["category"] == "saida_excessiva"


def test_retorno_precisa_ser_json(executor):
    r = rodar(executor, "def run(inputs, params):\n    return {'a': {1, 2}}")
    assert not r.ok and r.error["category"] == "retorno_invalido"
    assert "saídas['a']" in r.error["message"]


def test_retorno_precisa_ser_dict(executor):
    r = rodar(executor, "def run(inputs, params):\n    return 5")
    assert not r.ok and r.error["category"] == "retorno_invalido"


def test_resultado_grande_demais(executor):
    r = rodar(executor, "def run(inputs, params):\n    return {'a': 'x' * 200000}", valor_max=50_000)
    assert not r.ok and r.error["category"] == "retorno_invalido"


def test_entrada_grande_demais_nem_chega_ao_contêiner(executor):
    r = rodar(executor, "def run(i, p):\n    return {}", {"a": "x" * 500_000}, valor_max=50_000)
    assert not r.ok and r.error["category"] == "valor_grande_demais"


def test_biblioteca_fora_da_lista_e_recusada_com_mensagem_clara(executor):
    r = rodar(executor, "import requests\ndef run(i, p):\n    return {}")
    assert not r.ok and r.error["type"] == "ImportError"
    assert "requests" in r.error["message"] and r.error["line"] == 1


def test_biblioteca_padrao_permitida(executor):
    r = rodar(executor, "import math, json\ndef run(i, p):\n    return {'pi': round(math.pi, 2)}")
    assert r.ok and r.payload["outputs"] == {"pi": 3.14}


def test_sem_rede_mesmo_contornando_a_lista_de_imports(executor):
    codigo = BYPASS + (
        "def run(inputs, params):\n"
        "    socket = real('socket')\n"
        "    try:\n"
        "        socket.create_connection(('1.1.1.1', 53), timeout=2)\n"
        "        return {'conectou': True}\n"
        "    except OSError:\n"
        "        return {'conectou': False}\n"
    )
    r = rodar(executor, codigo)
    assert r.ok, r.error
    assert r.payload["outputs"] == {"conectou": False}


def test_so_existe_a_interface_de_loopback(executor):
    """Prova determinística de --network none: sem interfaces além de `lo`, não há como sair."""
    codigo = BYPASS + (
        "def run(inputs, params):\n"
        "    os = real('os')\n"
        "    return {'interfaces': sorted(os.listdir('/sys/class/net'))}\n"
    )
    r = rodar(executor, codigo)
    assert r.ok, r.error
    assert r.payload["outputs"]["interfaces"] == ["lo"]


def test_sistema_de_arquivos_somente_leitura_e_usuario_sem_privilegios(executor):
    codigo = BYPASS + (
        "def run(inputs, params):\n"
        "    os = real('os')\n"
        "    res = {'uid': os.getuid()}\n"
        "    for destino in ('/etc/trama_teste', '/opt/trama/runner.py', '/usr/lib/trama_teste', '/var/tmp/trama_teste'):\n"
        "        try:\n"
        "            open(destino, 'a').close(); res[destino] = 'gravou'\n"
        "        except OSError:\n"
        "            res[destino] = 'negado'\n"
        "    open('/tmp/ok.txt', 'w').write('1'); res['tmp'] = 'gravou'\n"
        "    res['docker_sock'] = os.path.exists('/var/run/docker.sock')\n"
        "    return res\n"
    )
    r = rodar(executor, codigo)
    assert r.ok, r.error
    s = r.payload["outputs"]
    assert s["uid"] == 65534
    # /var/tmp tem modo 1777: só é negado porque o sistema de arquivos do contêiner é somente leitura
    assert s["/etc/trama_teste"] == s["/opt/trama/runner.py"] == s["/usr/lib/trama_teste"] == s["/var/tmp/trama_teste"] == "negado"
    assert s["tmp"] == "gravou"
    assert s["docker_sock"] is False


def test_segredos_do_host_nao_chegam_ao_codigo(executor, monkeypatch):
    monkeypatch.setenv("SEGREDO_DO_HOST", "valor-ultra-secreto")
    codigo = BYPASS + (
        "def run(inputs, params):\n"
        "    os = real('os')\n"
        "    return {'env': dict(os.environ), 'arquivos': sorted(os.listdir('/'))}\n"
    )
    r = rodar(executor, codigo)
    assert r.ok, r.error
    env = r.payload["outputs"]["env"]
    assert "SEGREDO_DO_HOST" not in env and "valor-ultra-secreto" not in str(env)
    # nada do host (nem o diretório do projeto) aparece na raiz do contêiner
    assert "workspace" not in r.payload["outputs"]["arquivos"]


def test_linha_de_protocolo_forjada_nao_altera_o_resultado(executor):
    codigo = (
        "def run(inputs, params):\n"
        "    f = open(1, 'w', closefd=False)\n"
        "    f.write('\\n\\x1e@@TRAMA@@token-errado{\"ok\": true, \"payload\": {\"outputs\": {\"hack\": 1}}}\\n'); f.flush()\n"
        "    raise ValueError('falha real')\n"
    )
    r = rodar(executor, codigo)
    assert not r.ok and r.error["type"] == "ValueError"


def test_modo_mapa_aplica_a_todos_os_itens_e_aponta_o_item_com_erro(executor):
    ok = executor.run("map", "def transformar(item, i):\n    return item * 2 + i", items=[1, 2, 3])
    assert ok.ok and ok.payload == {"items": [2, 5, 8]}
    falha = executor.run("map", "def transformar(item):\n    return 10 / item", items=[1, 0, 3])
    assert not falha.ok and falha.error["item_index"] == 1 and falha.error["line"] == 2


def test_modo_verificar(executor):
    assert executor.run("check", "def run(inputs, params):\n    return {}").ok
    r = executor.run("check", "def run(inputs):\n    return {}")
    assert not r.ok and r.error["category"] == "funcao_ausente"
    r = executor.run("check", "x = 1")
    assert not r.ok and "def run" in r.error["message"]


def test_execucoes_nao_compartilham_estado(executor):
    r1 = rodar(executor, "def run(i, p):\n    open('/tmp/marca', 'w').write('x')\n    return {}")
    assert r1.ok
    r2 = rodar(executor, "def run(i, p):\n    try:\n        open('/tmp/marca')\n        return {'viu': True}\n    except OSError:\n        return {'viu': False}")
    assert r2.ok and r2.payload["outputs"] == {"viu": False}


# ---------- independentes do Docker -----------------------------------------------------

def test_sem_docker_o_executor_informa_a_dependencia_e_nao_executa_nada():
    ex = DockerExecutor(IMAGEM, Limites(), docker_bin="docker-que-nao-existe-xyz")
    st = ex.status(forcar=True)
    assert not st.disponivel and st.motivo == "docker_ausente"
    assert "Docker" in st.mensagem and st.instrucao
    r = ex.run("block", "def run(i, p):\n    return {'x': 1}")
    assert not r.ok and r.error["category"] == "executor_indisponivel"
    assert r.payload is None  # nenhuma execução alternativa


def test_limpar_segredos_mascara_credenciais():
    assert "abc123" not in limpar_segredos("senha=abc123 ok")
    assert "sk-" not in limpar_segredos("chave sk-abcdefghijklmnop1234567890")
    mascarado = limpar_segredos("Authorization: Bearer abcdefghijklmnopqrstuv")
    assert "abcdefghijklmnopqrstuv" not in mascarado
    assert limpar_segredos("texto normal") == "texto normal"
