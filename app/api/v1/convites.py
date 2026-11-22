from decimal import Decimal

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import exige_perfil
from app.models import TipoPerfil, Usuario
from app.models.enums import EstadoConvite
from app.services.match import manifestar_interesse

router = APIRouter(prefix="/convites", tags=["convites"])


class InteresseRequest(BaseModel):
    valor_proposto: Decimal | None = Field(
        default=None,
        gt=0,
        description="Contraproposta opcional do profissional (não pode ultrapassar +50% do valor inicial).",
    )


class ConviteInteresseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vaga_id: int
    profissional_id: int
    rodada: int
    posicao: int
    estado: EstadoConvite
    valor_proposto: Decimal | None


@router.post("/{convite_id}/interesse", response_model=ConviteInteresseResponse)
def registrar_interesse(
    convite_id: int,
    payload: InteresseRequest | None = Body(default=None),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(exige_perfil(TipoPerfil.PROFISSIONAL)),
) -> ConviteInteresseResponse:
    """Manifesta interesse em um convite, com contraproposta opcional."""
    valor_proposto = payload.valor_proposto if payload is not None else None
    convite = manifestar_interesse(
        db=db,
        convite_id=convite_id,
        usuario=usuario,
        valor_proposto=valor_proposto,
    )
    return ConviteInteresseResponse.model_validate(convite)