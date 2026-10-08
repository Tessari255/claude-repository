"""Aplicação FastAPI da Trama: API + (opcionalmente) o frontend já compilado."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from .api import Servicos, criar_router, resposta_de_erro, tratar_validacao
from .config import Settings, carregar_settings
from .engine import Motor
from .errors import ApiError
from .registry import Registro
from .sandbox import DockerExecutor
from .seed import semear_exemplos
from .store import Store

log = logging.getLogger("trama")

METODOS_COM_EFEITO = {"POST", "PUT", "PATCH", "DELETE"}
TAMANHO_MAX_CORPO = 6 * 1024 * 1024


CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
       "frame-ancestors 'none'")


def _hostname(valor: str) -> str:
    """'localhost:8000' → 'localhost'; '[::1]:8000' → '::1'."""
    try:
        return (urlparse("//" + valor).hostname or "").lower()
    except ValueError:
        return ""


class ProtecaoLocal(BaseHTTPMiddleware):
    """A Trama é local e de usuário único. Mesmo assim, uma página qualquer aberta no navegador não pode
    disparar ações na API nem ler respostas por DNS rebinding: só aceitamos Host local conhecido, exigimos JSON
    (força preflight de CORS, que não liberamos) e recusamos requisições de outra origem."""

    def __init__(self, app: Any, hosts_permitidos: set[str], origens_permitidas: set[str]) -> None:
        super().__init__(app)
        self.hosts = {h.strip("[]").lower() for h in hosts_permitidos}
        self.origens = origens_permitidas

    @staticmethod
    def _recusa(status: int, codigo: str, mensagem: str) -> JSONResponse:
        return JSONResponse(status_code=status, content=ApiError(status, codigo, mensagem).corpo())

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        host = request.headers.get("host", "")
        if _hostname(host) not in self.hosts:
            return self._recusa(400, "host_nao_permitido", "Endereço de acesso não permitido.")
        if request.method in METODOS_COM_EFEITO:
            origem = request.headers.get("origin")
            if origem and urlparse(origem).netloc != host and origem not in self.origens:
                return self._recusa(403, "origem_nao_permitida", "Requisição de outra origem recusada.")
            tamanho_txt = request.headers.get("content-length")
            if tamanho_txt is None and "transfer-encoding" in request.headers:
                return self._recusa(411, "tamanho_obrigatorio", "Informe o tamanho do conteúdo (Content-Length).")
            try:
                tamanho = int(tamanho_txt or 0)
            except ValueError:
                return self._recusa(400, "requisicao_invalida", "Content-Length inválido.")
            if tamanho > TAMANHO_MAX_CORPO:
                return self._recusa(413, "corpo_grande_demais", "O conteúdo enviado é grande demais.")
            if tamanho > 0 and not request.headers.get("content-type", "").startswith("application/json"):
                return self._recusa(415, "tipo_de_conteudo", "Envie o conteúdo como application/json.")
        resposta = await call_next(request)
        resposta.headers.setdefault("X-Frame-Options", "DENY")
        resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
        resposta.headers.setdefault("Referrer-Policy", "no-referrer")
        if not request.url.path.startswith("/api/"):
            resposta.headers.setdefault("Content-Security-Policy", CSP)
        return resposta


def criar_app(settings: Settings | None = None, executor: DockerExecutor | None = None) -> FastAPI:
    settings = settings or carregar_settings()
    store = Store(settings.db_path)
    registro = Registro(store)
    executor = executor or DockerExecutor(settings.imagem_executor, settings.limites, settings.docker_bin)
    motor = Motor(store, executor, registro, settings.limites)
    servicos = Servicos(settings, store, registro, executor, motor)

    @asynccontextmanager
    async def ciclo_de_vida(_: FastAPI):
        interrompidas = store.marcar_interrompidas()
        if interrompidas:
            log.warning("%d execução(ões) interrompida(s) por reinício foram marcadas como falhas.", interrompidas)
        status = executor.status()
        if status.disponivel:
            executor.cleanup_orphans()
        else:
            log.warning("Executor isolado indisponível (%s). Código personalizado ficará desabilitado. %s",
                        status.motivo, status.instrucao)
        if settings.semear_exemplos:
            semear_exemplos(store, registro, settings.pasta_exemplos)
        yield
        motor.encerrar()

    # A documentação interativa carrega assets de um CDN externo no navegador; fica desligada por padrão.
    docs = os.environ.get("TRAMA_DOCS") == "1"
    app = FastAPI(title="Trama", version="0.1.0", lifespan=ciclo_de_vida, redoc_url=None,
                  docs_url="/api/docs" if docs else None, openapi_url="/api/openapi.json" if docs else None)
    app.state.servicos = servicos
    app.add_middleware(ProtecaoLocal, hosts_permitidos=set(settings.hosts_permitidos),
                       origens_permitidas=set(settings.origens_permitidas))

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return resposta_de_erro(exc)

    @app.exception_handler(RequestValidationError)
    async def _validacao(request: Request, exc: RequestValidationError) -> JSONResponse:
        return tratar_validacao(request, exc)

    @app.exception_handler(UnicodeEncodeError)
    async def _texto_invalido(_: Request, exc: UnicodeEncodeError) -> JSONResponse:
        return JSONResponse(status_code=422, content=ApiError(
            422, "texto_invalido", "O conteúdo enviado tem caracteres que não podem ser gravados.").corpo())

    @app.exception_handler(Exception)
    async def _inesperado(_: Request, exc: Exception) -> JSONResponse:
        log.exception("Erro inesperado", exc_info=exc)
        return JSONResponse(status_code=500, content=ApiError(
            500, "erro_interno", "Ocorreu um erro interno. Tente novamente.").corpo())

    app.include_router(criar_router(servicos))

    pasta = settings.pasta_frontend
    if pasta.is_dir():
        app.mount("/assets", StaticFiles(directory=pasta / "assets"), name="assets")

        @app.get("/{caminho:path}", include_in_schema=False)
        def frontend(caminho: str) -> FileResponse:
            if caminho.startswith("api/"):
                raise ApiError(404, "nao_encontrado", "Recurso não encontrado.")
            try:
                arquivo = (pasta / caminho).resolve()
                if caminho and arquivo.is_file() and pasta.resolve() in arquivo.parents:
                    return FileResponse(arquivo)
            except (OSError, ValueError):  # ex.: byte NUL ou nome inválido no caminho
                pass
            return FileResponse(pasta / "index.html")

    return app
