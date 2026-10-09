"""Pool aquecido com o Docker de verdade: "um contêiner por trabalho" continua valendo, os limites do trabalho contam a
partir do recebimento, e o pool nunca atrapalha (some quando a API some, degrada para o caminho frio quando falha)."""

from __future__ import annotations

import dataclasses
import json
import re
import subprocess
import sys
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import criar_app
from app.sandbox import DockerExecutor
from app.sandbox.executor import ROTULO_POOL
from app.sandbox.pool import Aquecido

from .conftest import IMAGEM, LIMITES_TESTE, _montar, esperar_prontos
from .helpers import campo, estados, fluxo, passo, python_inline, ref, repeticoes, saida

pytestmark = pytest.mark.docker

HOSTNAME = "def run(inputs, params):\n    return {'h': open('/etc/hostname').read().strip()}"  # = ID do contêiner
BYPASS = "real = __import__.__closure__[0].cell_contents\n"


def docker(*args: str) -> set[str]:
    saida = subprocess.run(["docker", *args], capture_output=True, text=True).stdout
    return set(saida.split())


def ociosos() -> set[str]:
    """IDs dos contêineres do pool que estão parados esperando trabalho."""
    return docker("ps", "-q", "--filter", f"label={ROTULO_POOL}")


def vivos() -> set[str]:
    """Contêineres da Trama em execução (de trabalho ou do pool)."""
    return docker("ps", "-q", "--filter", "label=trama.executor")


def todos() -> set[str]:
    """Todo contêiner da Trama, vivo ou parado, de trabalho ou do pool."""
    return docker("ps", "-aq", "--filter", "label=trama.executor")


def host(r) -> str:
    assert r.ok, r.error
    return r.payload["outputs"]["h"]


def esperar_ate(condicao, limite_s=15.0, o_que="a condição"):
    fim = time.monotonic() + limite_s
    while not condicao():
        assert time.monotonic() < fim, f"{o_que} não se cumpriu em {limite_s:g} s"
        time.sleep(0.05)


@pytest.fixture()
def fazer(executor):
    """Fábrica de executores com pool. Ao fim de cada teste todos são encerrados e NADA da Trama pode sobrar no Docker."""
    criados: list[DockerExecutor] = []

    def _fazer(pool=2, ocioso_s=120.0, aquecer=True, docker_bin="docker") -> DockerExecutor:
        ex = DockerExecutor(IMAGEM, LIMITES_TESTE, docker_bin=docker_bin, pool=pool, pool_ocioso_s=ocioso_s)
        criados.append(ex)
        if aquecer:
            ex.aquecer()
            if pool and docker_bin == "docker":
                esperar_prontos(ex, pool)
        return ex

    executor.cleanup_orphans()  # como na inicialização da API: restos de uma API morta (ex.: a dos testes e2e) não contam
    yield _fazer
    for ex in criados:
        ex.encerrar()
    assert todos() == set(), "o teste deixou contêineres da Trama para trás"


# ------------------------------------------------------------------- um contêiner por trabalho
def test_cada_trabalho_roda_em_um_conteiner_distinto_que_ja_estava_iniciado(fazer):
    ex = fazer(pool=2)
    vistos = []
    for _ in range(6):
        prontos = ociosos()
        assert len(prontos) == 2
        h = host(ex.run("block", HOSTNAME))
        assert h in prontos  # rodou num contêiner que já existia antes do pedido
        vistos.append(h)
        esperar_prontos(ex, 2)
    assert len(set(vistos)) == 6  # nunca o mesmo contêiner duas vezes
    assert ex.estado_pool()["acertos"] == 6 and ex.estado_pool()["faltas"] == 0


def test_contêiner_usado_nao_volta_ao_pool_e_e_removido(fazer):
    ex = fazer(pool=1)
    (unico,) = ociosos()
    assert host(ex.run("block", HOSTNAME)) == unico
    assert unico not in todos()  # removido ao terminar
    esperar_prontos(ex, 1)
    (novo,) = ociosos()
    assert novo != unico
    for _ in range(10):  # e não reaparece
        assert unico not in todos()
        time.sleep(0.1)


def test_pool_repoe_depois_do_uso(fazer):
    ex = fazer(pool=2)
    antes = ociosos()
    assert host(ex.run("block", HOSTNAME)) in antes
    assert host(ex.run("block", HOSTNAME)) in antes
    esperar_prontos(ex, 2)
    depois = ociosos()
    assert len(depois) == 2 and depois.isdisjoint(antes)  # os dois que serviram morreram e foram substituídos


def test_trabalho_que_nao_cabe_no_contêiner_ocioso_vai_pelo_caminho_frio(fazer):
    """Memória, CPUs e processos são fixados no `docker run`: um trabalho com outro limite não pode usar o ocioso."""
    ex = fazer(pool=2)
    prontos = ociosos()
    menos_memoria = dataclasses.replace(LIMITES_TESTE, memoria_mb=64)
    mais_tempo = dataclasses.replace(LIMITES_TESTE, tempo_s=8.0)
    for lim in (menos_memoria, mais_tempo):
        h = host(ex.run("block", HOSTNAME, limits=lim))
        assert h not in prontos
    assert ociosos() == prontos  # o pool nem foi tocado
    assert ex.estado_pool()["acertos"] == 0 and ex.estado_pool()["faltas"] == 0


def test_trabalho_com_tempo_menor_que_o_padrao_usa_o_pool(fazer):
    """É o caso de produção: um passo com limite de tempo próprio (sempre menor ou igual ao padrão)."""
    ex = fazer(pool=2)
    prontos = ociosos()
    assert host(ex.run("block", HOSTNAME, limits=dataclasses.replace(LIMITES_TESTE, tempo_s=1.0))) in prontos


# ------------------------------------------------------------------- limites do trabalho
def test_limite_de_cpu_do_trabalho_e_apertado_como_no_caminho_frio(fazer):
    """O contêiner ocioso nasce com o limite de CPU do padrão; ao receber o trabalho ele é apertado para o valor que o
    caminho frio usaria. Lido de dentro do contêiner."""
    codigo = ("def run(inputs, params):\n"
              "    for linha in open('/proc/self/limits'):\n"
              "        if linha.startswith('Max cpu time'):\n"
              "            return {'h': ' '.join(linha.split()[3:5])}\n")
    frio = fazer(pool=0)
    quente = fazer(pool=2)
    for tempo_s, esperado in ((1.0, "4 5"), (2.5, "5 6"), (4.0, "7 8")):
        lim = dataclasses.replace(LIMITES_TESTE, tempo_s=tempo_s)
        assert host(frio.run("block", codigo, limits=lim)) == esperado
        assert host(quente.run("block", codigo, limits=lim)) == esperado
        esperar_prontos(quente, 2)
    assert quente.estado_pool()["acertos"] == 3


def test_vigia_e_tempo_do_trabalho_so_contam_a_partir_do_recebimento(fazer):
    """No caminho frio o vigia (6 s aqui) conta desde o início do contêiner. No pool o contêiner espera MAIS que isso
    e ainda assim o trabalho tem o limite inteiro: nem o vigia nem o tempo brando nem o de CPU começam antes do job."""
    lim = dataclasses.replace(LIMITES_TESTE, tempo_s=1.0, folga_inicio_s=0.0)  # vigia = 1 + 0 + 5 = 6 s
    ex = fazer(pool=2)
    prontos = ociosos()
    time.sleep(6.5)
    assert ociosos() == prontos  # os dois continuam esperando, vivos, mais velhos que o vigia
    r = ex.run("block", "import time\ndef run(inputs, params):\n    time.sleep(0.5)\n    return {'h': open('/etc/hostname').read().strip()}",
               limits=lim)
    assert host(r) in prontos
    # o limite brando do trabalho vale inteiro, contado do recebimento: ~1 s (nem 0, que seria a espera, nem o vigia)
    r = ex.run("block", "def run(inputs, params):\n    while True:\n        pass", limits=lim)
    assert not r.ok and r.error["category"] == "tempo_esgotado"
    assert 800 <= r.duration_ms < 4000
    assert ex.estado_pool()["acertos"] == 2


def test_runner_arma_memoria_e_temporizador_so_ao_receber_o_trabalho(fazer):
    """Lido de dentro do contêiner, depois de 3 s de espera ociosa: o limite de memória é o do trabalho e o temporizador
    brando ainda tem (quase) o tempo inteiro, em vez de ter começado a correr quando o contêiner subiu."""
    codigo = BYPASS + ("def run(inputs, params):\n"
                       "    sinal = real('signal')\n"
                       "    memoria = [l.split()[3:5] for l in open('/proc/self/limits') if l.startswith('Max address space')][0]\n"
                       "    return {'restante': sinal.getitimer(sinal.ITIMER_REAL)[0], 'intervalo': sinal.getitimer(sinal.ITIMER_REAL)[1],"
                       " 'memoria': memoria}\n")
    ex = fazer(pool=2)
    time.sleep(3)
    r = ex.run("block", codigo)
    assert r.ok, r.error
    saida = r.payload["outputs"]
    assert saida["memoria"] == [str(LIMITES_TESTE.memoria_mb * 1024 * 1024)] * 2  # RLIMIT_AS do trabalho
    assert LIMITES_TESTE.tempo_s - 0.5 < saida["restante"] <= LIMITES_TESTE.tempo_s  # 4 s, não 4 - 3
    assert saida["intervalo"] == 0.25  # re-armado a cada 250 ms, como no caminho frio
    assert ex.estado_pool()["acertos"] == 1


def test_vigia_do_trabalho_encerra_codigo_orfao_se_o_host_morrer_depois_do_recebimento(fazer):
    """Como o teste do vigia do caminho frio, mas com o contêiner do pool: o vigia nasce do recebimento do trabalho."""
    ex = fazer(pool=1, aquecer=False)
    lim = dataclasses.replace(LIMITES_TESTE, tempo_s=1.0, folga_inicio_s=0.0)  # vigia do trabalho = 6 s
    nome = "trama-teste-vigia-pool"
    job = {"token": "t", "mode": "block", "inputs": {}, "params": {}, "items": [], "limits": {
        "time_s": 1, "memory_mb": 128, "logs_max": 1024, "result_max": 1024, "rlimit_as": True},
        "code": BYPASS + "import time\ndef run(inputs, params):\n    s = real('signal')\n"
                "    s.setitimer(s.ITIMER_REAL, 0)\n    while True:\n        time.sleep(1)\n"}
    proc = subprocess.Popen(ex._comando_pool(nome), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        assert proc.stdout.readline().strip() == b"PRONTO"
        time.sleep(3)  # espera ociosa: se o vigia contasse daqui, o contêiner morreria 3 s antes do esperado
        enviado = time.monotonic()
        proc.stdin.write(f"{ex._vigia_s(lim)} {ex._cpu_brando(lim)}\n".encode() + json.dumps(job).encode())
        proc.stdin.close()
        for _ in range(40):
            if subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", nome], capture_output=True, text=True).stdout.strip() == "true":
                break
            time.sleep(0.1)
        time.sleep(1)
        proc.kill()  # a "API" morreu com o trabalho em andamento
        parou = None
        while parou is None and time.monotonic() - enviado < 25:
            r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}} {{.State.ExitCode}}", nome], capture_output=True, text=True)
            if r.stdout.split()[:1] == ["false"]:
                parou = (time.monotonic() - enviado, r.stdout.split()[1])
            time.sleep(0.25)
        assert parou is not None, "o contêiner continuou vivo depois que o host morreu"
        assert 5.0 <= parou[0] <= 10.0  # ~6 s depois do recebimento
        assert parou[1] == "137"  # morto pelo vigia (SIGKILL)
    finally:
        subprocess.run(["docker", "rm", "-f", nome], capture_output=True)


# ------------------------------------------------------------------- ocioso expira, API morre, desligamento
def test_ocioso_expira_e_e_substituido(fazer):
    ex = fazer(pool=1, ocioso_s=3.0)
    (velho,) = ociosos()
    esperar_ate(lambda: velho not in ociosos(), limite_s=8, o_que="a troca do contêiner ocioso")
    esperar_prontos(ex, 1)
    (novo,) = ociosos()
    assert novo != velho and velho not in todos()
    assert host(ex.run("block", HOSTNAME)) == novo  # e o substituto funciona


def test_contêiner_ocioso_se_mata_sozinho_mesmo_com_a_api_viva_e_sem_trabalho(fazer):
    """O vigia do ocioso não depende do host: o stdin continua aberto e ninguém chama `docker kill`."""
    ex = fazer(pool=1, ocioso_s=4.0, aquecer=False)
    nome = "trama-teste-ocioso"
    proc = subprocess.Popen(ex._comando_pool(nome), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        assert proc.stdout.readline().strip() == b"PRONTO"
        inicio = time.monotonic()
        proc.wait(timeout=15)
        assert 3.0 <= time.monotonic() - inicio <= 8.0  # ~4 s, o TRAMA_POOL_OCIOSO_S
        assert proc.returncode == 0
    finally:
        proc.kill()
        subprocess.run(["docker", "rm", "-f", nome], capture_output=True)


def test_api_morta_faz_os_ociosos_sumirem(fazer):
    """Um processo à parte (a "API") enche o pool e é morto com SIGKILL, sem nenhuma chance de limpar: os contêineres
    ociosos têm de sumir sozinhos, no máximo no tempo ocioso configurado."""
    ocioso_s = 8.0
    raiz = Path(__file__).resolve().parents[1]
    script = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(raiz)!r})
        from app.config import Limites
        from app.sandbox import DockerExecutor
        ex = DockerExecutor({IMAGEM!r}, Limites(tempo_s=4.0, memoria_mb=128, folga_inicio_s=5.0), pool=2, pool_ocioso_s={ocioso_s})
        ex.aquecer()
        while ex.estado_pool()["prontos"] < 2:
            time.sleep(0.05)
        print("PRONTO", flush=True)
        time.sleep(600)
    """)
    api = subprocess.Popen([sys.executable, "-I", "-c", script], stdout=subprocess.PIPE, text=True)
    try:
        assert api.stdout.readline().strip() == "PRONTO"
        assert len(ociosos()) == 2
        api.kill()
        api.wait()
        morreu = time.monotonic()
        esperar_ate(lambda: vivos() == set(), limite_s=ocioso_s + 5, o_que="o sumiço dos ociosos")
        assert time.monotonic() - morreu <= ocioso_s + 5
        # sem `--rm` (o motivo do encerramento precisa ser lido), os parados ficam até o saneamento da próxima inicialização
        assert fazer(pool=0).cleanup_orphans() == 2
    finally:
        api.kill()
        if restos := todos():
            subprocess.run(["docker", "rm", "-f", *restos], capture_output=True)


def test_desligar_remove_todos_os_ociosos(fazer):
    ex = fazer(pool=2)
    assert len(ociosos()) == 2
    ex.encerrar()
    assert todos() == set()
    assert ex.estado_pool()["prontos"] == 0


def test_desligar_a_api_remove_os_ociosos(fazer, settings):
    ex = fazer(pool=2, aquecer=False)
    with TestClient(criar_app(settings, ex)):
        esperar_prontos(ex, 2)  # o lifespan ligou o pool
        assert len(ociosos()) == 2
    assert todos() == set()  # lifespan encerrado


def test_saneamento_da_inicializacao_cobre_os_ociosos_que_uma_api_morta_deixou(fazer):
    orfa = fazer(pool=2)  # faz de conta que é uma API anterior que morreu sem limpar
    assert len(ociosos()) == 2
    nova = fazer(pool=0)
    assert nova.cleanup_orphans() == 2
    assert todos() == set()
    assert len(host(orfa.run("block", HOSTNAME))) == 12  # a órfã descarta os mortos e segue (caminho frio ou reposição)


# ------------------------------------------------------------------- pool=0 e falhas do Docker
LEGADO = [
    "docker", "run", "-i", "--name", "n", "--label", "trama.executor=1", "--pull", "never", "--log-driver", "none",
    "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m", "--shm-size", "16m",
    "--memory", "192m", "--memory-swap", "192m", "--cpus", "1.0", "--pids-limit", "64", "--ulimit", "cpu=7:8",
    "--ulimit", "nofile=256:256", "--ulimit", "fsize=16777216", "--ulimit", "core=0", "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges", "--user", "65534:65534", "--ipc", "none", IMAGEM, "14", "python", "-I",
    "/opt/trama/runner.py",
]


def test_pool_zero_se_comporta_como_o_antigo(fazer):
    ex = fazer(pool=0)
    assert ex._comando("n", LIMITES_TESTE) == LEGADO  # o comando do caminho frio é exatamente o de antes
    for _ in range(2):
        host(ex.run("block", HOSTNAME))
        assert ociosos() == set()  # nunca existe contêiner esperando
    assert ex.estado_pool() == {"configurado": 0, "prontos": 0, "ocioso_s": 120.0, "acertos": 0, "faltas": 0}
    assert not [t for t in threading.enumerate() if t.name.startswith("trama-pool")]


def test_docker_ausente_nao_derruba_o_pool_e_nada_roda_sem_isolamento(fazer):
    ex = fazer(pool=2, docker_bin="docker-que-nao-existe-xyz")
    time.sleep(1)
    assert ex.estado_pool()["prontos"] == 0
    r = ex.run("block", HOSTNAME)
    assert not r.ok and r.error["category"] == "executor_indisponivel" and r.payload is None


def test_falha_ao_repor_o_pool_degrada_para_o_caminho_frio_sem_erro(fazer, tmp_path):
    """O `docker` falha só quando é para subir contêiner do pool (--entrypoint): o resto do Docker funciona."""
    recusa = tmp_path / "docker-sem-pool"
    recusa.write_text('#!/bin/sh\nfor a in "$@"; do [ "$a" = "--entrypoint" ] && exit 1; done\nexec docker "$@"\n')
    recusa.chmod(0o755)
    ex = fazer(pool=2, docker_bin=str(recusa))
    for _ in range(3):
        assert len(host(ex.run("block", HOSTNAME))) == 12  # funciona, sem nenhum erro para o usuário
    assert ex.estado_pool()["prontos"] == 0 and ex.estado_pool()["acertos"] == 0 and ex.estado_pool()["faltas"] == 3
    assert ociosos() == set()


def test_contêiner_aquecido_que_morreu_sem_receber_o_trabalho_cai_no_caminho_frio(fazer, monkeypatch):
    """O cliente do contêiner já tinha encerrado quando o trabalho foi escrito: o JSON não chegou ao runner, então é
    seguro (e invisível para o usuário) rodar o mesmo trabalho num contêiner novo."""
    ex = fazer(pool=1, aquecer=False)
    morto = subprocess.Popen(["true"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    morto.wait()
    monkeypatch.setattr(ex._pool, "retirar", lambda: Aquecido("trama-que-nao-existe", morto, time.monotonic()))
    assert len(host(ex.run("block", HOSTNAME))) == 12


def test_ociosos_mortos_por_fora_nao_atrapalham_nem_viram_erro(fazer):
    ex = fazer(pool=2)
    subprocess.run(["docker", "rm", "-f", *ociosos()], capture_output=True)  # o daemon reiniciou, alguém limpou...
    assert len(host(ex.run("block", HOSTNAME))) == 12  # o trabalho roda mesmo assim (pool ou caminho frio)
    esperar_prontos(ex, 2)
    assert len(ociosos()) == 2


@pytest.mark.parametrize("nome, codigo, limites, categoria", [
    ("memoria", "def run(inputs, params):\n    x = bytearray(600 * 1024 * 1024)\n"
                "    for i in range(0, len(x), 4096):\n        x[i] = 1\n    return {}",
     {"rlimit_as": False}, "memoria_excedida"),
    ("tempo", BYPASS + "def run(inputs, params):\n    s = real('signal')\n    s.setitimer(s.ITIMER_REAL, 0)\n"
                       "    while True:\n        pass", {"tempo_s": 1.0, "folga_inicio_s": 2.0}, "tempo_esgotado"),
    ("morte", BYPASS + "def run(inputs, params):\n    os = real('os')\n    os.kill(os.getpid(), 9)",
     {}, "encerrado_pelo_sistema"),
])
def test_trabalho_que_mata_o_conteiner_nao_envenena_o_pool(fazer, nome, codigo, limites, categoria):
    ex = fazer(pool=2)
    antes = ociosos()
    r = ex.run("block", codigo, limits=dataclasses.replace(LIMITES_TESTE, **limites))
    assert not r.ok and r.error["category"] == categoria
    esperar_prontos(ex, 2)
    depois = ociosos()
    assert len(antes & depois) == 1 and len(depois) == 2  # o morto saiu do Docker e foi reposto
    assert (antes - depois).isdisjoint(todos())
    assert host(ex.run("block", HOSTNAME)) in depois  # o próximo trabalho roda normal num contêiner saudável
    assert ex.estado_pool()["acertos"] == 2


# ------------------------------------------------------------------- concorrência e teto
def test_quatro_trabalhos_concorrentes_e_teto_de_contêineres_vivos(fazer):
    ex = fazer(pool=2)
    codigo = ("import time\ndef run(inputs, params):\n    t0 = time.time()\n    time.sleep(1.5)\n"
              "    return {'h': open('/etc/hostname').read().strip(), 't0': t0, 't1': time.time()}")
    maior = 0
    parar = threading.Event()

    def amostrar():
        nonlocal maior
        while not parar.is_set():
            maior = max(maior, len(docker("ps", "-q", "--filter", "label=trama.executor")))
            time.sleep(0.1)

    vigia = threading.Thread(target=amostrar, daemon=True)
    vigia.start()
    try:
        with ThreadPoolExecutor(6) as pool:
            rs = [f.result() for f in [pool.submit(ex.run, "block", codigo) for _ in range(6)]]
    finally:
        parar.set()
        vigia.join()
    saidas = [r.payload["outputs"] for r in rs if r.ok]
    assert len(saidas) == 6, [r.error for r in rs if not r.ok]
    assert len({s["h"] for s in saidas}) == 6  # seis contêineres diferentes
    eventos = sorted([(s["t0"], 1) for s in saidas] + [(s["t1"], -1) for s in saidas])
    simultaneos = atual = 0
    for _, delta in eventos:
        atual += delta
        simultaneos = max(simultaneos, atual)
    assert simultaneos == 4  # teto de contêineres de TRABALHO em andamento
    assert 4 <= maior <= 4 + 2  # vivos no total: no máximo os 4 de trabalho + os 2 ociosos do pool
    assert ex.estado_pool()["acertos"] >= 2


# ------------------------------------------------------------------- API e motor
def test_sistema_expoe_o_estado_do_pool_sem_dados_sensiveis(fazer, settings):
    ex = fazer(pool=2)
    with TestClient(criar_app(settings, ex)) as c:
        esperar_prontos(ex, 2)
        ids = ociosos()
        resposta = c.get("/api/sistema")
        corpo, texto = resposta.json(), resposta.text
    assert set(corpo["pool"]) == {"configurado", "prontos", "ocioso_s", "acertos", "faltas"}
    assert corpo["pool"]["configurado"] == 2 and corpo["pool"]["prontos"] == 2 and corpo["pool"]["ocioso_s"] == 120.0
    assert len(ids) == 2 and not [i for i in ids if i in texto]  # nenhum ID de contêiner
    assert not re.search(r"trama-[0-9a-f]{12}", texto)  # nem nome de contêiner


def test_sistema_com_pool_desligado(fazer, settings):
    ex = fazer(pool=0)
    with TestClient(criar_app(settings, ex)) as c:
        assert c.get("/api/sistema").json()["pool"] == {"configurado": 0, "prontos": 0, "ocioso_s": 120.0, "acertos": 0, "faltas": 0}


def test_laco_com_python_usa_um_contêiner_por_item_e_respeita_o_tempo_do_passo(fazer, settings):
    """Pelo motor, como na aplicação: o limite de tempo do passo (menor que o padrão) também usa o pool."""
    ex = fazer(pool=2)
    amb = _montar(settings, ex)
    p = python_inline("p", "def run(inputs, params):\n    return {'h': open('/etc/hostname').read().strip(), 'n': inputs['n'] * 2}",
                      {"n": ("numero", ref("laco", "item"))}, {"h": "texto", "n": "numero"}, timeout=2)
    f = fluxo([passo("laco", "builtin.para_cada", {"lista": ref("gatilho", "l")}, {"limite": 5}, slots={"corpo": [p]}),
               saida("s", "fim", ref("gatilho", "l"))], [campo("l", "lista", [1, 2, 3, 4, 5])])
    run = amb.executar(f)
    assert run["state"] == "concluido", run["error"]
    reps = repeticoes(run, "p")
    assert sorted(r["outputs"]["n"] for r in reps.values()) == [2, 4, 6, 8, 10]
    assert len({r["outputs"]["h"] for r in reps.values()}) == 5  # cada item, um contêiner
    assert estados(run)["laco"] == "concluido"
    assert ex.estado_pool()["acertos"] >= 1


def test_ociosos_tem_rotulo_proprio_e_nao_aparecem_como_contêiner_de_trabalho(fazer):
    """Quem olha o Docker distingue o que espera (pool) do que trabalha; o saneamento cobre os dois pela chave."""
    fazer(pool=1)
    assert len(ociosos()) == 1
    assert docker("ps", "-q", "--filter", "label=trama.executor=1") == set()
    assert todos() == ociosos()
