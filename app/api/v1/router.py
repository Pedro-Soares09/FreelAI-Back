from fastapi import APIRouter

from app.api.v1 import saude

# Roteador agregador da versão 1 da API. Cada módulo registra o seu roteador aqui.
api_router = APIRouter(prefix="/api/v1")
api_router.include_router(saude.router)
