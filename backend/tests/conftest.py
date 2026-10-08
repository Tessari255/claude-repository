"""Fixtures compartilhadas.

Os testes que dependem do Docker real são marcados com ``docker``. Se o executor
isolado não estiver disponível no ambiente, eles são PULADOS (com o motivo) — nunca
substituídos por execução sem isolamento.
"""

from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Limites, Settings  # noqa: E402
from app.sandbox import DockerExecutor  # noqa: E402

IMAGEM = os.environ.get("TRAMA_EXECUTOR_IMAGE", "trama-executor:2")
LIMITES_TESTE = Limites(tempo_s=4.0, memoria_mb=128, folga_inicio_s=5.0)


@pytest.fixture(scope="session")
def executor() -> DockerExecutor:
    ex = DockerExecutor(IMAGEM, LIMITES_TESTE)
    st = ex.status(forcar=True)
    if not st.disponivel:
        pytest.skip(f"Executor isolado indisponível ({st.motivo}): {st.mensagem}")
    return ex


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
