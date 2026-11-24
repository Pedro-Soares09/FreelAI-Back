from fastapi import APIRouter

from app.api.v1 import (
    acordo,
    admin,
    admin_aprovacoes,
    arquivos,
    auth,
    avaliacoes,
    conversas,
    denuncias,
    disputas,
    notificacoes,
    pagamentos,
    perfis,
    profissional,
    saude,
    stubs,
    vagas,
    verificacao,
)

# Roteador agregador da versão 1 da API. Cada módulo registra o seu roteador aqui.
# Importar `pagamentos` e `avaliacoes` também registra as varreduras de prazo.
api_router = APIRouter(prefix="/api/v1")
api_router.include_router(saude.router)
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(admin_aprovacoes.router)
api_router.include_router(arquivos.router)
api_router.include_router(avaliacoes.router)
api_router.include_router(conversas.router)
api_router.include_router(denuncias.router)
api_router.include_router(disputas.router)
api_router.include_router(notificacoes.router)
api_router.include_router(pagamentos.router)
api_router.include_router(perfis.router)
api_router.include_router(verificacao.router)
api_router.include_router(profissional.router)
api_router.include_router(vagas.router)
api_router.include_router(acordo.router)
api_router.include_router(stubs.router)
