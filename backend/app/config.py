"""Configuração da Trama, lida de variáveis de ambiente (prefixo TRAMA_)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]


def _int(nome: str, padrao: int) -> int:
    try:
        return int(os.environ.get(nome, padrao))
    except ValueError:
        return padrao


def _float(nome: str, padrao: float) -> float:
    try:
        return float(os.environ.get(nome, padrao))
    except ValueError:
        return padrao


@dataclass(frozen=True)
class Limites:
    """Limites aplicados a cada execução de código personalizado e aos dados do fluxo."""

    tempo_s: float = 10.0
    memoria_mb: int = 256
    cpus: float = 1.0
    max_processos: int = 64
    logs_max: int = 64 * 1024
    valor_max: int = 1024 * 1024  # tamanho de cada valor trafegado entre blocos
    itens_max_lista: int = 10_000  # teto absoluto do bloco "Para cada item"
    # Tempo extra para o contêiner iniciar/encerrar antes do abate forçado.
    folga_inicio_s: float = 6.0
    # Somente para testes do abate pelo cgroup; produção mantém True.
    rlimit_as: bool = True


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: RAIZ / "data")
    imagem_executor: str = "trama-executor:2"
    docker_bin: str = "docker"
    limites: Limites = field(default_factory=Limites)
    # Pool aquecido: contêineres já iniciados esperando o trabalho (0 desliga). Cada um atende UM trabalho e morre.
    pool_tamanho: int = 2
    pool_ocioso_s: float = 120.0  # vida máxima de um contêiner parado sem receber trabalho
    hosts_permitidos: tuple[str, ...] = ("localhost", "127.0.0.1", "[::1]", "testserver")
    origens_permitidas: tuple[str, ...] = ()
    semear_exemplos: bool = False  # os modelos aparecem na tela inicial; só semeia projetos prontos se TRAMA_SEED_EXAMPLES=1
    pasta_frontend: Path = field(default_factory=lambda: RAIZ / "frontend" / "dist")
    pasta_exemplos: Path = field(default_factory=lambda: RAIZ / "examples")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "trama.db"


POOL_MAXIMO = 8


def carregar_settings() -> Settings:
    pool_ocioso_s = _float("TRAMA_POOL_OCIOSO_S", Settings.pool_ocioso_s)
    limites = Limites(
        tempo_s=_float("TRAMA_TIMEOUT_S", 10.0),
        memoria_mb=_int("TRAMA_MEMORY_MB", 256),
        cpus=_float("TRAMA_CPUS", 1.0),
        max_processos=_int("TRAMA_PIDS", 64),
        logs_max=_int("TRAMA_LOGS_KB", 64) * 1024,
        valor_max=_int("TRAMA_VALUE_KB", 1024) * 1024,
        itens_max_lista=_int("TRAMA_MAX_LIST_ITEMS", 10_000),
    )
    hosts = os.environ.get("TRAMA_ALLOWED_HOSTS")
    return Settings(
        data_dir=Path(os.environ.get("TRAMA_DATA_DIR", RAIZ / "data")),
        imagem_executor=os.environ.get("TRAMA_EXECUTOR_IMAGE", "trama-executor:2"),
        docker_bin=os.environ.get("TRAMA_DOCKER_BIN", "docker"),
        limites=limites,
        pool_tamanho=min(max(_int("TRAMA_POOL", Settings.pool_tamanho), 0), POOL_MAXIMO),
        pool_ocioso_s=pool_ocioso_s if pool_ocioso_s > 0 else Settings.pool_ocioso_s,
        hosts_permitidos=tuple(h.strip() for h in hosts.split(",")) if hosts else Settings().hosts_permitidos,
        origens_permitidas=tuple(o.strip() for o in os.environ.get("TRAMA_ALLOWED_ORIGINS", "").split(",") if o.strip()),
        semear_exemplos=os.environ.get("TRAMA_SEED_EXAMPLES", "0") == "1",
    )
