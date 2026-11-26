import asyncio
import contextlib
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute
from jose import JWTError

from app.api.v1.router import api_router
from app.config import settings
from app.erros import (
    ErroDominio,
    handler_erro_dominio,
    handler_erro_inesperado,
    handler_http_exception,
    handler_validacao_pydantic,
)
from app.routers import auth, mock
from app.security import decode_access_token
from app.services import agendador

logger = logging.getLogger("freelai.requisicoes")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)
logger.propagate = False


def _extrair_usuario_id(request: Request) -> int | None:
    cabecalho = request.headers.get("authorization", "")
    if not cabecalho.lower().startswith("bearer "):
        return None
    token = cabecalho[7:].strip()
    if not token:
        return None
    try:
        carga = decode_access_token(token)
        return int(carga["sub"])
    except (JWTError, KeyError, ValueError):
        return None


def _id_da_operacao(rota: APIRoute) -> str:
    """Nome estável e legível para cada operação, usado na geração de tipos do front."""
    prefixo = rota.tags[0] if rota.tags else "api"
    return f"{prefixo}_{rota.name}"


@asynccontextmanager
async def ciclo_de_vida(_app: FastAPI):
    """Liga o agendador das varreduras na subida e o encerra de forma limpa no shutdown."""
    tarefa = None
    if settings.varreduras_automaticas:
        tarefa = asyncio.create_task(agendador.laco())
    try:
        yield
    finally:
        if tarefa is not None:
            tarefa.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tarefa


app = FastAPI(
    lifespan=ciclo_de_vida,
    title="FreelAI Back",
    version="1.0.0",
    description="API do FreelAI: contratantes e profissionais em serviços pontuais.",
    generate_unique_id_function=_id_da_operacao,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Stub"],
)


@app.middleware("http")
async def middleware_log_requisicao(request: Request, call_next):
    inicio = time.perf_counter()
    status_code = 500
    try:
        resposta = await call_next(request)
        status_code = resposta.status_code
        return resposta
    finally:
        duracao_ms = round((time.perf_counter() - inicio) * 1000, 2)
        usuario_id = _extrair_usuario_id(request)
        linha_log = json.dumps(
            {
                "method": request.method,
                "path": request.url.path,
                "status": status_code,
                "duration_ms": duracao_ms,
                "user_id": usuario_id,
            },
            ensure_ascii=False,
        )
        logger.info(linha_log)


app.add_exception_handler(ErroDominio, handler_erro_dominio)
app.add_exception_handler(HTTPException, handler_http_exception)
app.add_exception_handler(RequestValidationError, handler_validacao_pydantic)
app.add_exception_handler(Exception, handler_erro_inesperado)

app.include_router(api_router)

# Rotas anteriores à versão 1, mantidas enquanto o front migra para /api/v1.
# Ficam fora do contrato OpenAPI para não entrarem nos tipos gerados.
app.include_router(auth.router, include_in_schema=False)
app.include_router(mock.router, include_in_schema=False)


@app.get("/health", include_in_schema=False)
def health_check() -> dict[str, str]:
    return {"status": "ok"}
