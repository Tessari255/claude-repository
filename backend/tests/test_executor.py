"""Prova os limites e o isolamento do executor (critérios 2, 7 e 8 + requisitos de segurança)."""

from __future__ import annotations

import dataclasses
import json
import subprocess
import time

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


def test_limites_de_recursos_estao_aplicados_no_cgroup_do_contêiner(executor):
    """Lê, de dentro do contêiner, os limites que o kernel realmente aplica (cgroup v1 ou v2)."""
    codigo = BYPASS + (
        "def run(inputs, params):\n"
        "    os = real('os')\n"
        "    def ler(*caminhos):\n"
        "        for c in caminhos:\n"
        "            try:\n"
        "                return open(c).read().strip()\n"
        "            except OSError:\n"
        "                pass\n"
        "    return {\n"
        "      'memoria': ler('/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory/memory.limit_in_bytes'),\n"
        "      'swap': ler('/sys/fs/cgroup/memory.swap.max', '/sys/fs/cgroup/memory/memory.memsw.limit_in_bytes'),\n"
        "      'pids': ler('/sys/fs/cgroup/pids.max', '/sys/fs/cgroup/pids/pids.max'),\n"
        "      'cpu': ler('/sys/fs/cgroup/cpu.max', '/sys/fs/cgroup/cpu/cpu.cfs_quota_us'),\n"
        "    }\n"
    )
    r = rodar(executor, codigo, memoria_mb=128)
    assert r.ok, r.error
    c = r.payload["outputs"]
    assert int(c["memoria"]) == (128 + 64) * 1024 * 1024          # limite de memória (com folga do interpretador)
    assert c["swap"] in ("0", str((128 + 64) * 1024 * 1024))       # sem swap extra
    assert int(c["pids"]) == LIMITES_TESTE.max_processos            # no máximo N processos/threads
    assert c["cpu"].split()[0] == str(int(LIMITES_TESTE.cpus * 100000))  # cota de CPU (1 CPU = 100000 µs por 100000)


def test_sem_capacidades_sem_novos_privilegios_e_com_seccomp(executor):
    codigo = BYPASS + (
        "def run(inputs, params):\n"
        "    campos = {}\n"
        "    for linha in open('/proc/self/status'):\n"
        "        k, _, v = linha.partition(':')\n"
        "        if k in ('CapEff', 'CapPrm', 'CapBnd', 'NoNewPrivs', 'Seccomp'):\n"
        "            campos[k] = v.strip()\n"
        "    return campos\n"
    )
    r = rodar(executor, codigo)
    assert r.ok, r.error
    c = r.payload["outputs"]
    assert int(c["CapEff"], 16) == 0 and int(c["CapPrm"], 16) == 0 and int(c["CapBnd"], 16) == 0  # --cap-drop ALL
    assert c["NoNewPrivs"] == "1"
    assert c["Seccomp"] == "2"  # filtro seccomp padrão do Docker ativo


def test_bomba_de_processos_e_contida_pelo_limite_de_pids(executor):
    codigo = BYPASS + (
        "def run(inputs, params):\n"
        "    os = real('os')\n"
        "    filhos = 0\n"
        "    for _ in range(500):\n"
        "        try:\n"
        "            pid = os.fork()\n"
        "        except OSError:\n"
        "            break\n"
        "        if pid == 0:\n"
        "            real('time').sleep(30)\n"
        "            os._exit(0)\n"
        "        filhos += 1\n"
        "    return {'filhos': filhos}\n"
    )
    r = rodar(executor, codigo, tempo_s=8)
    assert r.ok, r.error
    assert 0 < r.payload["outputs"]["filhos"] < LIMITES_TESTE.max_processos  # o kernel recusou o resto
    vivos = subprocess.run(["docker", "ps", "-q", "--filter", "label=trama.executor=1"], capture_output=True, text=True).stdout.split()
    assert vivos == []  # e nada sobrou rodando depois


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


# ---------- achados da revisão independente ----------------------------------------------------

FORJAR = BYPASS + (
    "def run(inputs, params):\n"
    "    import collections, json\n"
    "    token = collections._sys._getframe(1).f_locals['trabalho']['token']\n"
    "    f = open(1, 'w', closefd=False)\n"
    "    f.write('\\n\\x1e@@TRAMA@@' + token + json.dumps(%s) + '\\n'); f.flush()\n"
    "    real('os')._exit(0)\n"
)


def test_resposta_forjada_pelo_codigo_do_usuario_e_saneada_pelo_host(executor):
    """O código roda no mesmo processo do runner e consegue achar o token: o host não pode confiar em nada."""
    forjado = (
        '{"ok": False, "error": {"category": "excecao", "type": {"a": 1}, "message": "x" * 5000, "line": {"a": 1},'
        ' "snippet": ["x"], "traceback": 5, "item_index": "1"},'
        ' "logs": [{"source": "__proto__", "text": "invisivel"}, {"source": "stdout", "text": 5}, "lixo",'
        ' {"source": "stderr", "text": "A" * 500000}]}'
    )
    r = rodar(executor, FORJAR % forjado)
    assert not r.ok
    e = r.error
    assert e["category"] == "excecao" and e["type"] == "" and e["line"] is None and e["snippet"] is None
    assert len(e["message"]) <= 2000 and e["traceback"] == "" and "item_index" not in e
    assert [p["source"] for p in r.logs] == ["stderr"]                       # fonte inválida/entradas inválidas descartadas
    assert sum(len(p["text"]) for p in r.logs) <= LIMITES_TESTE.logs_max      # e o total respeita o limite de logs
    assert all(isinstance(p["text"], str) for p in r.logs)


def test_resposta_forjada_com_formato_invalido_vira_erro_interno(executor):
    r = rodar(executor, FORJAR % '{"ok": True, "payload": {"outputs": "nao-e-dict"}}')
    assert not r.ok and r.error["category"] == "erro_interno"
    r = rodar(executor, FORJAR % '{"ok": False, "error": {"category": "executor_indisponivel", "message": "fingindo"}}')
    assert r.error["category"] == "excecao"  # categorias fora da lista conhecida não passam


def test_texto_com_separadores_unicode_de_linha_nao_derruba_o_bloco(executor):
    texto = "a\u2028b\u2029c\u0085d\x0be\x0cf\x1cg"
    r = rodar(executor, "def run(inputs, params):\n    print(inputs['t'])\n    return {'t': inputs['t'], 'n': len(inputs['t'])}", {"t": texto})
    assert r.ok, r.error
    assert r.payload["outputs"] == {"t": texto, "n": len(texto)} and texto in r.logs[0]["text"]


def test_verificacao_de_codigo_concorda_com_a_execucao(executor):
    assert not executor.run("check", "return 1\ndef run(inputs, params):\n    return {}").ok       # compile() recusa
    assert executor.run("check", "run = lambda inputs, params: {}").ok                                 # executável de verdade
    r = executor.run("check", "def run(inputs, params):\n    return {}\nraise ValueError('no módulo')")
    assert not r.ok and r.error["type"] == "ValueError" and r.error["line"] == 3
    r = executor.run("check", "while True:\n    pass\ndef run(inputs, params):\n    return {}", limits=dataclasses.replace(LIMITES_TESTE, tempo_s=1))
    assert not r.ok and r.error["category"] == "tempo_esgotado"


def test_vigia_dentro_do_contêiner_encerra_codigo_orfao_se_o_host_morrer(executor):
    """Mata o cliente `docker run` (simula a API morrendo) e confere que o contêiner se encerra sozinho."""
    lim = dataclasses.replace(LIMITES_TESTE, tempo_s=1.0, folga_inicio_s=0.0)  # vigia = 1 + 0 + 5 = 6 s
    nome = "trama-teste-vigia"
    cmd = executor._comando(nome, lim)
    job = {"token": "t", "mode": "block", "inputs": {}, "params": {}, "items": [], "limits": {
        "time_s": 1, "memory_mb": 128, "logs_max": 1024, "result_max": 1024, "rlimit_as": True},
        "code": BYPASS + "import time\ndef run(inputs, params):\n    s = real('signal')\n"
                "    s.setitimer(s.ITIMER_REAL, 0)\n    while True:\n        time.sleep(1)\n"}
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(job).encode())
        proc.stdin.close()
        r = subprocess.CompletedProcess([], 1, "", "")
        for _ in range(40):  # espera o contêiner existir e estar rodando
            r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", nome], capture_output=True, text=True)
            if r.stdout.strip() == "true":
                break
            time.sleep(0.25)
        assert r.stdout.strip() == "true"
        proc.kill()  # a "API" morreu: ninguém mais vai chamar docker kill
        parou = False
        for _ in range(60):
            r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}} {{.State.ExitCode}}", nome], capture_output=True, text=True)
            if r.stdout.split()[0] == "false":
                parou = True
                break
            time.sleep(0.5)
        assert parou, "o contêiner continuou vivo depois que o host morreu"
        assert r.stdout.split()[1] == "137"  # morto pelo vigia (SIGKILL)
    finally:
        subprocess.run(["docker", "rm", "-f", nome], capture_output=True)


def test_encerramento_externo_que_nao_e_falta_de_memoria_nao_e_chamado_de_memoria(executor):
    r = rodar(executor, BYPASS + "def run(inputs, params):\n    os = real('os')\n    os.kill(os.getpid(), 9)\n")
    assert not r.ok and r.error["category"] == "encerrado_pelo_sistema"


def test_estourar_o_tempo_de_cpu_sem_o_aviso_brando_e_tempo_esgotado_nao_memoria(executor):
    codigo = BYPASS + ("def run(inputs, params):\n    s = real('signal')\n    s.setitimer(s.ITIMER_REAL, 0)\n"
                       "    while True:\n        pass\n")
    r = rodar(executor, codigo, tempo_s=1.0, folga_inicio_s=30.0)  # o host espera; quem corta é o limite de CPU
    assert not r.ok and r.error["category"] == "tempo_esgotado" and "processamento" in r.error["message"]


def test_comando_do_docker_tem_todas_as_restricoes(executor):
    cmd = " ".join(executor._comando("x", LIMITES_TESTE))
    for flag in ("--network none", "--read-only", "--cap-drop ALL", "--security-opt no-new-privileges", "--user 65534:65534",
                 "--pull never", "--log-driver none", "--ulimit core=0", "--ipc none", "--pids-limit"):
        assert flag in cmd, flag
    assert "-v " not in cmd and "--volume" not in cmd and "--env" not in cmd and "-e " not in cmd  # nada do host entra
    assert "--rm" not in cmd  # removido explicitamente, depois de ler o motivo do encerramento
