"""Aplicação FastAPI da Trama: API + (opcionalmente) o frontend já compilado."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

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


class ProtecaoLocal(BaseHTTPMiddleware):
    """A Trama é local e de usuário único. Mesmo assim, uma página qualquer aberta no navegador
    não pode disparar ações na API: exigimos JSON (força preflight de CORS, que não liberamos)
    e recusamos requisições de outra origem."""

    def __init__(self, app: Any, origens_permitidas: set[str]) -> None:
        super().__init__(app)
        self.origens = origens_permitidas

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        if request.method in METODOS_COM_EFEITO:
            origem = request.headers.get("origin")
            if origem and urlparse(origem).netloc != request.headers.get("host") and origem not in self.origens:
                return JSONResponse(status_code=403, content=ApiError(
                    403, "origem_nao_permitida", "Requisição de outra origem recusada.").corpo())
            tamanho = int(request.headers.get("content-length") or 0)
            if tamanho > TAMANHO_MAX_CORPO:
                return JSONResponse(status_code=413, content=ApiError(
                    413, "corpo_grande_demais", "O conteúdo enviado é grande demais.").corpo())
            if tamanho > 0 and not request.headers.get("content-type", "").startswith("application/json"):
                return JSONResponse(status_code=415, content=ApiError(
                    415, "tipo_de_conteudo", "Envie o conteúdo como application/json.").corpo())
        return await call_next(request)


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

    app = FastAPI(title="Trama", version="0.1.0", lifespan=ciclo_de_vida,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.state.servicos = servicos
    app.add_middleware(ProtecaoLocal, origens_permitidas=set(settings.origens_permitidas))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.hosts_permitidos))

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return resposta_de_erro(exc)

    @app.exception_handler(RequestValidationError)
    async def _validacao(request: Request, exc: RequestValidationError) -> JSONResponse:
        return tratar_validacao(request, exc)

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
            arquivo = (pasta / caminho).resolve()
            if caminho and arquivo.is_file() and pasta.resolve() in arquivo.parents:
                return FileResponse(arquivo)
            return FileResponse(pasta / "index.html")

    return app
