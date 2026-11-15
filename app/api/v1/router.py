from fastapi import APIRouter

from app.api.v1 import admin, auth, saude

# Roteador agregador da versão 1 da API. Cada módulo registra o seu roteador aqui.
api_router = APIRouter(prefix="/api/v1")
api_router.include_router(saude.router)
api_router.include_router(auth.router)
api_router.include_router(admin.router)
