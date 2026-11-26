"""Filas de aprovação da administração: cadastros, experiências, qualificações e EPIs."""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.schemas.cadastro import DecisaoAprovacaoEntrada, ItemFilaAprovacao
from app.db import get_db
from app.deps import exige_admin
from app.models import StatusAprovacao
from app.services import cadastro

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(exige_admin)])

TipoAprovacao = Literal["profissional", "contratante", "experiencia", "qualificacao", "epi"]


@router.get("/aprovacoes", response_model=list[ItemFilaAprovacao])
def listar_aprovacoes(
    tipo: TipoAprovacao | None = Query(default=None),
    estado: StatusAprovacao | None = Query(default=StatusAprovacao.PENDENTE),
    db: Session = Depends(get_db),
) -> list[ItemFilaAprovacao]:
    """Fila unificada de aprovações; por padrão só os itens pendentes."""
    return cadastro.listar_fila(db, tipo=tipo, estado=estado)


@router.post("/aprovacoes/{tipo}/{item_id}/decisao", response_model=ItemFilaAprovacao)
def decidir_aprovacao(
    tipo: TipoAprovacao,
    item_id: int,
    entrada: DecisaoAprovacaoEntrada,
    db: Session = Depends(get_db),
) -> ItemFilaAprovacao:
    """Aprova ou recusa (com motivo). Aprovar a verificação ativa a conta pendente."""
    return cadastro.decidir_aprovacao(
        db, tipo, item_id, aprovar=entrada.decisao == "aprovar", motivo=entrada.motivo
    )
