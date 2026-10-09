"""Lógica do pool de contêineres aquecidos, sem Docker: os "contêineres" são processos `sleep` de verdade, o que permite
provar reposição, descarte de mortos e vencidos, espera depois de falhas e desligamento de forma rápida e determinística."""

from __future__ import annotations

import dataclasses
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Limites, carregar_settings
from app.main import criar_app
from app.sandbox import DockerExecutor
from app.sandbox.executor import CHAVE_ROTULO, ROTULO, ROTULO_POOL, SCRIPT_DO_POOL
from app.sandbox.pool import Aquecido, PoolAquecido


class Fabrica:
    """Cria e descarta "contêineres" (processos `sleep`) e guarda o que aconteceu com cada um."""

    def __init__(self) -> None:
        self.criados: list[Aquecido] = []
        self.descartados: list[Aquecido] = []
        self.falhar = False
        self.explodir = False
        self.segurar: threading.Event | None = None  # enquanto não for liberado, iniciar() fica esperando
        self.tentativas = 0

    def iniciar(self) -> Aquecido | None:
        self.tentativas += 1
        if self.segurar is not None:
            self.segurar.wait(10)
        if self.explodir:
            raise RuntimeError("falha inesperada de teste")
        if self.falhar:
            return None
        proc = subprocess.Popen(["sleep", "60"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        q = Aquecido(f"c{len(self.criados)}", proc, time.monotonic())
        self.criados.append(q)
        return q

    def descartar(self, lista: list[Aquecido]) -> None:
        for q in lista:
            q.proc.kill()
            q.proc.wait()
            self.descartados.append(q)

    def limpar(self) -> None:
        for q in self.criados:
            if q.proc.poll() is None:
                q.proc.kill()
            q.proc.wait()
            for f in (q.proc.stdin, q.proc.stdout, q.proc.stderr):
                if f:
                    f.close()


@pytest.fixture()
def fabrica():
    f = Fabrica()
    yield f
    if f.segurar is not None:
        f.segurar.set()
    f.limpar()


def esperar(condicao, limite_s=10.0):
    fim = time.monotonic() + limite_s
    while not condicao():
        assert time.monotonic() < fim, "a condição não se cumpriu a tempo"
        time.sleep(0.01)


def montar(fabrica, tamanho=2, ocioso_s=120.0) -> PoolAquecido:
    return PoolAquecido(tamanho, ocioso_s, fabrica.iniciar, fabrica.descartar)


def test_pool_enche_sozinho_ate_o_tamanho_e_informa_o_estado(fabrica):
    pool = montar(fabrica, tamanho=3)
    assert pool.estado()["prontos"] == 0  # nada acontece antes de ativar
    pool.ativar()
    esperar(lambda: pool.estado()["prontos"] == 3)
    assert pool.estado() == {"configurado": 3, "prontos": 3, "ocioso_s": 120.0, "acertos": 0, "faltas": 0}
    pool.encerrar()


def test_quem_sai_do_pool_nunca_volta_e_o_pool_repoe(fabrica):
    pool = montar(fabrica)
    pool.ativar()
    esperar(lambda: pool.estado()["prontos"] == 2)
    a, b = pool.retirar(), pool.retirar()
    assert a is not None and b is not None and a is not b
    assert (a.nome, b.nome) == ("c0", "c1")  # o mais antigo primeiro
    esperar(lambda: pool.estado()["prontos"] == 2)
    repostos = [pool.retirar(), pool.retirar()]
    assert all(q is not None for q in repostos)
    assert {q.nome for q in repostos}.isdisjoint({a.nome, b.nome})
    assert a not in fabrica.descartados and a.proc.poll() is None  # o pool não mexe em quem já saiu
    assert pool.estado()["acertos"] == 4
    pool.encerrar()


def test_pool_vazio_devolve_none_na_hora_e_conta_a_falta(fabrica):
    pool = montar(fabrica, tamanho=1)
    assert pool.retirar() is None  # ainda não ativado
    assert pool.estado()["faltas"] == 1
    pool.encerrar()


def test_reposicao_lenta_nao_bloqueia_quem_pede_um_trabalho(fabrica):
    pool = montar(fabrica, tamanho=1)
    pool.ativar()
    esperar(lambda: pool.estado()["prontos"] == 1)
    fabrica.segurar = threading.Event()  # a próxima partida fica presa (Docker lento)
    assert pool.retirar() is not None
    esperar(lambda: fabrica.tentativas == 2)
    inicio = time.monotonic()
    assert pool.retirar() is None  # vazio e repondo: o pedido não espera a partida
    assert time.monotonic() - inicio < 0.5
    fabrica.segurar.set()
    esperar(lambda: pool.estado()["prontos"] == 1)
    pool.encerrar()


def test_contêiner_que_morreu_ocioso_e_descartado_e_substituido(fabrica):
    pool = montar(fabrica, tamanho=2)
    pool.ativar()
    esperar(lambda: pool.estado()["prontos"] == 2)
    morto = fabrica.criados[0]
    morto.proc.kill()
    morto.proc.wait()
    esperar(lambda: morto in fabrica.descartados)
    esperar(lambda: pool.estado()["prontos"] == 2)
    assert len(fabrica.criados) == 3
    entregues = [pool.retirar(), pool.retirar()]
    assert morto not in entregues and all(q is not None and q.proc.poll() is None for q in entregues)
    pool.encerrar()


def test_morto_que_ainda_estava_no_pool_nao_e_entregue(fabrica):
    pool = montar(fabrica, tamanho=2)
    pool.ativar()
    esperar(lambda: pool.estado()["prontos"] == 2)
    primeiro = fabrica.criados[0]
    primeiro.proc.kill()
    primeiro.proc.wait()
    escolhido = pool.retirar()  # pode chegar antes da varredura em segundo plano
    assert escolhido is not None and escolhido is not primeiro
    pool.encerrar()


def test_ocioso_vence_e_e_trocado_por_um_novo(fabrica):
    pool = montar(fabrica, tamanho=1, ocioso_s=1.2)  # o host troca um pouco antes dos 1,2 s do contêiner
    pool.ativar()
    esperar(lambda: pool.estado()["prontos"] == 1)
    velho = fabrica.criados[0]
    esperar(lambda: velho in fabrica.descartados, limite_s=5)
    esperar(lambda: pool.estado()["prontos"] == 1)
    assert fabrica.criados[-1] is not velho and fabrica.criados[-1].proc.poll() is None
    pool.encerrar()


def test_falhas_seguidas_fazem_o_pool_esperar_mais_e_o_trabalho_segue_sem_erro(fabrica):
    fabrica.falhar = True
    pool = montar(fabrica, tamanho=1)
    pool.ativar()
    esperar(lambda: fabrica.tentativas >= 1)
    assert pool.retirar() is None  # sem pronto, o chamador usa o caminho frio
    time.sleep(1.6)
    assert fabrica.tentativas <= 3  # espera 1 s, depois 2 s: não fica martelando o Docker
    fabrica.falhar = False
    esperar(lambda: pool.estado()["prontos"] == 1, limite_s=8)  # voltou sozinho
    pool.encerrar()


def test_excecao_ao_iniciar_conta_como_falha_e_nao_derruba_a_reposicao(fabrica):
    fabrica.explodir = True
    pool = montar(fabrica, tamanho=1)
    pool.ativar()
    esperar(lambda: fabrica.tentativas >= 1)
    assert pool.retirar() is None
    fabrica.explodir = False
    esperar(lambda: pool.estado()["prontos"] == 1, limite_s=8)
    pool.encerrar()


def test_encerrar_descarta_os_prontos_e_quem_terminava_de_subir(fabrica):
    pool = montar(fabrica, tamanho=2)
    pool.ativar()
    esperar(lambda: pool.estado()["prontos"] == 2)
    fabrica.segurar = threading.Event()
    assert pool.retirar() is not None  # abre uma vaga: a reposição começa e fica presa
    esperar(lambda: fabrica.tentativas == 3)
    liberar = threading.Timer(0.3, fabrica.segurar.set)
    liberar.start()
    pool.encerrar()  # espera a partida em andamento terminar e a descarta
    liberar.join()
    restantes = [q for q in fabrica.criados if q not in fabrica.descartados]
    assert len(restantes) == 1  # só o que saiu do pool (e agora é do chamador)
    assert pool.estado()["prontos"] == 0
    assert pool.retirar() is None


def test_ativar_de_novo_depois_de_encerrar_religa_o_pool(fabrica):
    pool = montar(fabrica, tamanho=1)
    pool.ativar()
    esperar(lambda: pool.estado()["prontos"] == 1)
    pool.encerrar()
    assert pool.estado()["prontos"] == 0
    pool.ativar()
    esperar(lambda: pool.estado()["prontos"] == 1)
    pool.encerrar()


def test_tamanho_zero_nao_cria_thread_nem_contêiner(fabrica):
    antes = {t.name for t in threading.enumerate()}
    pool = montar(fabrica, tamanho=0)
    pool.ativar()
    assert pool.retirar() is None
    assert fabrica.tentativas == 0 and fabrica.criados == []
    assert {t.name for t in threading.enumerate()} == antes
    assert pool.estado()["configurado"] == 0
    pool.encerrar()


# ---------------------------------------------------------------- comando e script do contêiner ocioso (sem Docker)
IMAGEM = "trama-executor:teste"
LIMITES = Limites(tempo_s=4.0, memoria_mb=128, folga_inicio_s=5.0)


def test_comando_do_pool_tem_as_mesmas_restricoes_do_caminho_frio():
    ex = DockerExecutor(IMAGEM, LIMITES, pool=2, pool_ocioso_s=45)
    frio, pool = ex._comando("x", LIMITES), ex._comando_pool("x")
    opcoes_frias, opcoes_pool = frio[:frio.index(IMAGEM)], pool[:pool.index("--entrypoint")]
    i = opcoes_frias.index("--label") + 1
    assert [*opcoes_frias[:i], ROTULO_POOL, *opcoes_frias[i + 1:]] == opcoes_pool  # a única diferença é o rótulo
    texto = " ".join(pool)
    for flag in ("--network none", "--read-only", "--cap-drop ALL", "--security-opt no-new-privileges", "--user 65534:65534",
                 "--pull never", "--log-driver none", "--ulimit core=0", "--ipc none", "--pids-limit", "--memory-swap"):
        assert flag in texto, flag
    assert "-v " not in texto and "--volume" not in texto and "--env" not in texto and "-e " not in texto
    assert "--rm" not in texto
    assert pool[pool.index("--entrypoint"):] == ["--entrypoint", "bash", IMAGEM, "-c", SCRIPT_DO_POOL, "bash", "45.000"]
    assert ROTULO_POOL.startswith(CHAVE_ROTULO + "=") and ROTULO.startswith(CHAVE_ROTULO + "=")  # o saneamento cobre os dois


@pytest.fixture()
def script_do_pool(tmp_path):
    """Roda SCRIPT_DO_POOL no bash do host, com um `timeout` de mentira que só mostra o que recebeu e os limites de CPU."""
    (tmp_path / "timeout").write_text('#!/bin/sh\necho "timeout $*"\necho "cpu $(ulimit -St) $(ulimit -Ht)"\n')
    (tmp_path / "timeout").chmod(0o755)

    def rodar(entrada: bytes | None, ocioso_s: str = "30", fechar: bool = True, limite_s: float = 10):
        env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "LC_ALL": "C"}
        proc = subprocess.Popen(["bash", "-c", SCRIPT_DO_POOL, "bash", ocioso_s], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        inicio = time.monotonic()
        if entrada is not None:
            proc.stdin.write(entrada)
            proc.stdin.flush()
        if fechar:
            proc.stdin.close()
        saida = proc.stdout.read()  # termina quando o script sai
        proc.wait(timeout=limite_s)
        espera = time.monotonic() - inicio
        erro = proc.stderr.read()
        if not fechar:
            proc.stdin.close()
        return proc.returncode, saida.decode().splitlines(), erro.decode(), espera

    return rodar


def test_script_do_pool_vira_o_vigia_do_trabalho_e_aperta_o_limite_de_cpu(script_do_pool):
    codigo, linhas, erro, _ = script_do_pool(b"14 7\n{\"trabalho\": 1}")
    assert codigo == 0, erro
    assert linhas[0] == "PRONTO"  # avisa o host de que subiu
    assert linhas[1] == "timeout -s KILL 14 python -I /opt/trama/runner.py"  # o vigia do trabalho, igual ao do caminho frio
    assert linhas[2] == "cpu 7 8"  # CPU: soft = tempo + 3, hard = soft + 1


def test_script_do_pool_nao_consome_o_trabalho_alem_da_linha_de_partida():
    """O JSON que vem depois da linha de partida tem de chegar intacto ao runner (que lê o stdin inteiro)."""
    script = SCRIPT_DO_POOL.replace("exec timeout -s KILL \"$vigia\" python -I /opt/trama/runner.py", "cat")
    r = subprocess.run(["bash", "-c", script, "bash", "30"], input=b"14 7\n" + b'{"a": "b\xc3\xa7"}\nresto', capture_output=True, timeout=10)
    assert r.stdout == b'PRONTO\n{"a": "b\xc3\xa7"}\nresto'


@pytest.mark.parametrize("partida", [b"14; touch /tmp/trama-invasao 7\n", b"$(id) 7\n", b"14\n", b"abc def\n", b"-1 7\n", b"14 -7\n"])
def test_script_do_pool_recusa_linha_de_partida_que_nao_sejam_dois_numeros(script_do_pool, partida):
    codigo, linhas, _, _ = script_do_pool(partida)
    assert codigo == 64
    assert linhas == ["PRONTO"]  # nunca chegou ao exec do runner
    assert not Path("/tmp/trama-invasao").exists()


def test_script_do_pool_sai_se_o_host_fecha_o_stdin_sem_mandar_trabalho(script_do_pool):
    codigo, linhas, _, _ = script_do_pool(None)
    assert codigo == 0 and linhas == ["PRONTO"]


def test_script_do_pool_sai_sozinho_quando_o_tempo_ocioso_acaba(script_do_pool):
    codigo, linhas, _, espera = script_do_pool(None, ocioso_s="1.5", fechar=False)
    assert codigo == 0 and linhas == ["PRONTO"]
    assert 1.3 <= espera < 5  # o stdin continuou aberto: quem encerrou foi o tempo ocioso


@pytest.mark.parametrize("ambiente, tamanho, ocioso_s", [
    ({}, 2, 120.0),
    ({"TRAMA_POOL": "0"}, 0, 120.0),
    ({"TRAMA_POOL": "4", "TRAMA_POOL_OCIOSO_S": "30"}, 4, 30.0),
    ({"TRAMA_POOL": "99"}, 8, 120.0),       # teto para não encher o computador de contêineres parados
    ({"TRAMA_POOL": "-3"}, 0, 120.0),
    ({"TRAMA_POOL": "abc", "TRAMA_POOL_OCIOSO_S": "xyz"}, 2, 120.0),
    ({"TRAMA_POOL_OCIOSO_S": "0"}, 2, 120.0),
    ({"TRAMA_POOL_OCIOSO_S": "-5"}, 2, 120.0),
])
def test_variaveis_de_ambiente_do_pool(monkeypatch, ambiente, tamanho, ocioso_s):
    for nome in ("TRAMA_POOL", "TRAMA_POOL_OCIOSO_S"):
        monkeypatch.delenv(nome, raising=False)
    for nome, valor in ambiente.items():
        monkeypatch.setenv(nome, valor)
    s = carregar_settings()
    assert (s.pool_tamanho, s.pool_ocioso_s) == (tamanho, ocioso_s)


def test_a_aplicacao_monta_o_executor_com_o_pool_das_configuracoes(settings):
    sem_docker = dataclasses.replace(settings, pool_tamanho=3, pool_ocioso_s=45.0, docker_bin="docker-que-nao-existe-xyz")
    app = criar_app(sem_docker)
    assert app.state.servicos.executor.estado_pool() == {"configurado": 3, "prontos": 0, "ocioso_s": 45.0, "acertos": 0, "faltas": 0}
    with TestClient(app) as c:  # Docker ausente: o pool não liga e a API informa o estado sem erro
        assert c.get("/api/sistema").json()["pool"]["configurado"] == 3
        assert c.get("/api/sistema").json()["pool"]["prontos"] == 0
