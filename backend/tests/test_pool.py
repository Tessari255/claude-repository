"""Lógica do pool de contêineres aquecidos, sem Docker: os "contêineres" são processos `sleep` de verdade, o que permite
provar reposição, descarte de mortos e vencidos, espera depois de falhas e desligamento de forma rápida e determinística."""

from __future__ import annotations

import subprocess
import threading
import time

import pytest

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
