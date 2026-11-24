from fastapi import APIRouter

from app.api.v1 import admin, auth, notificacoes, pagamentos, saude, stubs, vagas

# Roteador agregador da versão 1 da API. Cada módulo registra o seu roteador aqui.
# Importar `pagamentos` e `notificacoes` também registra as varreduras de prazo.
api_router = APIRouter(prefix="/api/v1")
api_router.include_router(saude.router)
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(notificacoes.router)
api_router.include_router(pagamentos.router)
api_router.include_router(vagas.router)
api_router.include_router(stubs.router)
