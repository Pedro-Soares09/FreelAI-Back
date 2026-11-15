from fastapi import APIRouter
from pydantic import BaseModel

from app.config import settings

router = APIRouter(tags=["saude"])


class RespostaSaude(BaseModel):
    status: str
    perfil: str


@router.get("/health", response_model=RespostaSaude)
def verificar_saude() -> RespostaSaude:
    """Indica que a API está no ar e qual perfil de execução está ativo."""
    return RespostaSaude(status="ok", perfil=settings.perfil)
