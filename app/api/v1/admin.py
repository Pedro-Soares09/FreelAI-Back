from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import exige_admin
from app.models import Denuncia, EstadoDenuncia, OrigemDenuncia
from app.services import configuracao, varreduras

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(exige_admin)])


class ResultadoVarredura(BaseModel):
    executado_em: str
    alteracoes: dict[str, int] = Field(description="Registros alterados por varredura.")


class ConfiguracoesEfetivas(BaseModel):
    perfil: str
    valores: dict[str, float]


class DenunciaAdminResumo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    denunciante_id: int | None
    denunciado_id: int
    vaga_id: int | None
    conversa_id: int | None
    origem: OrigemDenuncia
    motivo: str
    descricao: str | None
    estado: EstadoDenuncia
    decisao_motivo: str | None
    criado_em: datetime
    decidido_em: datetime | None


@router.post("/prazos/varrer", response_model=ResultadoVarredura)
def varrer_prazos(db: Session = Depends(get_db)) -> dict:
    """Força a verificação imediata de tudo que depende de prazo (expirações e liberações)."""
    return varreduras.executar_varreduras(db)


@router.get("/configuracoes", response_model=ConfiguracoesEfetivas)
def consultar_configuracoes(db: Session = Depends(get_db)) -> ConfiguracoesEfetivas:
    """Prazos, pesos e limites em vigor para o perfil de execução atual."""
    return ConfiguracoesEfetivas(perfil=configuracao.perfil_atual(), valores=configuracao.todas(db))


@router.get("/denuncias", response_model=list[DenunciaAdminResumo])
def listar_denuncias(
    estado: EstadoDenuncia | None = Query(default=None),
    origem: OrigemDenuncia | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[Denuncia]:
    """Lista denúncias registradas no sistema (incluindo automáticas no chat)."""
    consulta = select(Denuncia).order_by(Denuncia.id.desc())
    if estado is not None:
        consulta = consulta.where(Denuncia.estado == estado)
    if origem is not None:
        consulta = consulta.where(Denuncia.origem == origem)
    return list(db.scalars(consulta).all())
