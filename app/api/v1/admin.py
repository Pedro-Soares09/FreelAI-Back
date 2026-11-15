from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import exige_admin
from app.services import configuracao, varreduras

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(exige_admin)])


class ResultadoVarredura(BaseModel):
    executado_em: str
    alteracoes: dict[str, int] = Field(description="Registros alterados por varredura.")


class ConfiguracoesEfetivas(BaseModel):
    perfil: str
    valores: dict[str, float]


@router.post("/prazos/varrer", response_model=ResultadoVarredura)
def varrer_prazos(db: Session = Depends(get_db)) -> dict:
    """Força a verificação imediata de tudo que depende de prazo (expirações e liberações)."""
    return varreduras.executar_varreduras(db)


@router.get("/configuracoes", response_model=ConfiguracoesEfetivas)
def consultar_configuracoes(db: Session = Depends(get_db)) -> ConfiguracoesEfetivas:
    """Prazos, pesos e limites em vigor para o perfil de execução atual."""
    return ConfiguracoesEfetivas(perfil=configuracao.perfil_atual(), valores=configuracao.todas(db))
