from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.config import settings
from app.db import Base, engine
from app.routers import auth, mock

# Criação provisória das tabelas existentes, até as migrações assumirem o esquema.
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="FreelAI Back",
    version="1.0.0",
    description="API do FreelAI: contratantes e profissionais em serviços pontuais.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

# Rotas anteriores à versão 1, mantidas enquanto o front migra para /api/v1.
app.include_router(auth.router)
app.include_router(mock.router)


@app.get("/health", tags=["saude"], deprecated=True)
def health_check() -> dict[str, str]:
    return {"status": "ok"}
