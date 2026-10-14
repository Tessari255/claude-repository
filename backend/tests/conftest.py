"""Fixtures compartilhadas.

Os testes que dependem do Docker real são marcados com ``docker``. Se o executor
isolado não estiver disponível no ambiente, eles são PULADOS (com o motivo) — nunca
substituídos por execução sem isolamento.
"""

from __future__ import annotations

import dataclasses
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Limites, Settings
from app.sandbox import DockerExecutor

IMAGEM = os.environ.get("TRAMA_EXECUTOR_IMAGE", "trama-executor:2")
LIMITES_TESTE = Limites(tempo_s=4.0, memoria_mb=128, folga_inicio_s=5.0)


@pytest.fixture(scope="session")
def executor() -> DockerExecutor:
    ex = DockerExecutor(IMAGEM, LIMITES_TESTE)
    st = ex.status(forcar=True)
    if not st.disponivel:
        pytest.skip(f"Executor isolado indisponível ({st.motivo}): {st.mensagem}")
    return ex


def esperar_prontos(ex: DockerExecutor, quantos: int, limite_s: float = 30.0) -> None:
    """Espera o pool do executor ter ``quantos`` contêineres ociosos prontos (a reposição roda em segundo plano)."""
    fim = time.monotonic() + limite_s
    while ex.estado_pool()["prontos"] < quantos:
        assert time.monotonic() < fim, f"o pool não chegou a {quantos} prontos: {ex.estado_pool()}"
        time.sleep(0.05)


@pytest.fixture(scope="module")
def executor_pool() -> Iterator[DockerExecutor]:
    """Executor com o pool aquecido ligado. Por módulo: os ociosos são removidos quando o módulo termina e não aparecem
    em módulos que olham o Docker."""
    ex = DockerExecutor(IMAGEM, LIMITES_TESTE, pool=2)
    st = ex.status(forcar=True)
    if not st.disponivel:
        pytest.skip(f"Executor isolado indisponível ({st.motivo}): {st.mensagem}")
    ex.aquecer()
    esperar_prontos(ex, 2)
    yield ex
    ex.encerrar()


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return dataclasses.replace(
        Settings(), data_dir=tmp_path / "dados", imagem_executor=IMAGEM,
        limites=LIMITES_TESTE, semear_exemplos=False,
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "docker: exige o executor isolado (Docker) disponível")


# ---------------------------------------------------------------------------- ambientes do motor
from types import SimpleNamespace  # noqa: E402

from app.engine import Motor  # noqa: E402
from app.models import Flow  # noqa: E402
from app.registry import Registro  # noqa: E402
from app.store import Store  # noqa: E402


def _montar(settings, executor):
    store = Store(settings.db_path)
    registro = Registro(store)
    motor = Motor(store, executor, registro, settings.limites)

    def executar(flow: dict, dados_gatilho: dict | None = None) -> dict:
        run_id = motor.preparar(Flow.model_validate(flow), None, dados_gatilho)
        motor.rodar(run_id)
        return store.obter_execucao(run_id)

    return SimpleNamespace(store=store, registro=registro, motor=motor, executor=executor, executar=executar)


@pytest.fixture()
def sem_docker(settings):
    """Motor cujo executor isolado está indisponível (prova que nada roda sem isolamento)."""
    ex = DockerExecutor(IMAGEM, LIMITES_TESTE, docker_bin="docker-que-nao-existe-xyz")
    return _montar(settings, ex)


@pytest.fixture()
def com_docker(settings, executor):
    return _montar(settings, executor)


# ---------------------------------------------------------------------------- clientes HTTP
from fastapi.testclient import TestClient  # noqa: E402

from app.main import criar_app  # noqa: E402


def cliente(settings, executor) -> TestClient:
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
