from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from app.api.v1.router import api_router
from app.config import settings
from app.routers import auth, mock


def _id_da_operacao(rota: APIRoute) -> str:
    """Nome estável e legível para cada operação, usado na geração de tipos do front."""
    prefixo = rota.tags[0] if rota.tags else "api"
    return f"{prefixo}_{rota.name}"


app = FastAPI(
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

app.include_router(api_router)

# Rotas anteriores à versão 1, mantidas enquanto o front migra para /api/v1.
# Ficam fora do contrato OpenAPI para não entrarem nos tipos gerados.
app.include_router(auth.router, include_in_schema=False)
app.include_router(mock.router, include_in_schema=False)


@app.get("/health", include_in_schema=False)
def health_check() -> dict[str, str]:
    return {"status": "ok"}
