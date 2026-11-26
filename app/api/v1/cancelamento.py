"""Cancelamento de vaga e prévia dos efeitos (advertência por cancelamento tardio)."""

from datetime import datetime

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import exige_conta_ativa
from app.erros import erro_http
from app.models import Usuario, Vaga
from app.services import cancelamento

router = APIRouter(tags=["cancelamento"])


class PreviaCancelamento(BaseModel):
    pode_cancelar: bool
    gera_advertencia: bool
    limite_horas: float
    horas_para_inicio: float
    advertencias_no_periodo: int
    advertencias_para_suspensao: int


class CancelamentoEntrada(BaseModel):
    motivo: str = Field(min_length=3, max_length=255)


class CancelamentoResposta(BaseModel):
    estado: str
    advertencia_aplicada: bool
    advertencias_no_periodo: int
    valor_estornado: bool
    suspensa_ate: datetime | None = None


def _vaga(db: Session, vaga_id: int) -> Vaga:
    vaga = db.get(Vaga, vaga_id)
    if vaga is None:
        raise erro_http(status.HTTP_404_NOT_FOUND, "vaga_nao_encontrada", "Vaga não encontrada.")
    return vaga


@router.get("/vagas/{vaga_id}/cancelamento", response_model=PreviaCancelamento)
def consultar_cancelamento(
    vaga_id: int, usuario: Usuario = Depends(exige_conta_ativa), db: Session = Depends(get_db)
) -> dict:
    """Mostra se a vaga pode ser cancelada agora e se isso gera advertência."""
    return cancelamento.previa(db, _vaga(db, vaga_id), usuario)


@router.post("/vagas/{vaga_id}/cancelamento", response_model=CancelamentoResposta)
def cancelar_vaga(
    vaga_id: int,
    entrada: CancelamentoEntrada,
    usuario: Usuario = Depends(exige_conta_ativa),
    db: Session = Depends(get_db),
) -> dict:
    """Cancela a vaga; cancelamento tardio gera advertência e pode suspender a conta."""
    return cancelamento.cancelar(db, _vaga(db, vaga_id), usuario, entrada.motivo)
