"""Rotas de disputa (contestação em 24h pelo contratante e consulta da disputa da vaga) — US61."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.v1.pagamentos import contratante_ativo
from app.api.v1.schemas.disputas import DisputaDetalhe, DisputaEntrada
from app.db import get_db
from app.deps import usuario_atual
from app.erros import erro_http
from app.models import Usuario, Vaga
from app.services import disputas

router = APIRouter(tags=["disputas"])


def _buscar_vaga(db: Session, vaga_id: int) -> Vaga:
    vaga = db.get(Vaga, vaga_id)
    if vaga is None:
        raise erro_http(status.HTTP_404_NOT_FOUND, "vaga_nao_encontrada", "Vaga não encontrada.")
    return vaga


@router.post(
    "/vagas/{vaga_id}/disputa",
    response_model=DisputaDetalhe,
    status_code=status.HTTP_201_CREATED,
)
def contestar_servico(
    vaga_id: int,
    entrada: DisputaEntrada,
    usuario: Usuario = Depends(contratante_ativo),
    db: Session = Depends(get_db),
) -> DisputaDetalhe:
    """Abre a contestação dentro das 24h de confirmação e trava a vaga em `em_disputa`."""
    vaga = _buscar_vaga(db, vaga_id)
    disputa = disputas.abrir_disputa(db, vaga, usuario, descricao=entrada.descricao)
    return disputas.montar_detalhe(db, disputa, vaga)


@router.get("/vagas/{vaga_id}/disputa", response_model=DisputaDetalhe)
def consultar_disputa(
    vaga_id: int,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> DisputaDetalhe:
    """Consulta o estado da disputa da vaga e os lançamentos do pagamento."""
    vaga = _buscar_vaga(db, vaga_id)
    return disputas.consultar_disputa_da_vaga(db, vaga, usuario)
