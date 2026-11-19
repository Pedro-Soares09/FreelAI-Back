"""Rotas de avaliação mútua (48h), consulta de avaliações e reputação 70/20/10 (US55)."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.v1.schemas.avaliacoes import (
    AvaliacaoDetalhe,
    AvaliacaoEntrada,
    AvaliacoesDaVaga,
    ReputacaoDetalhe,
)
from app.db import get_db
from app.deps import usuario_atual, usuario_opcional
from app.erros import erro_http
from app.models import Usuario, Vaga
from app.services import avaliacoes

router = APIRouter(tags=["avaliacoes"])


def _buscar_vaga(db: Session, vaga_id: int) -> Vaga:
    vaga = db.get(Vaga, vaga_id)
    if vaga is None:
        raise erro_http(status.HTTP_404_NOT_FOUND, "vaga_nao_encontrada", "Vaga não encontrada.")
    return vaga


def _buscar_usuario(db: Session, usuario_id: int) -> Usuario:
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND, "usuario_nao_encontrado", "Usuário não encontrado."
        )
    return usuario


@router.post(
    "/vagas/{vaga_id}/avaliacoes",
    response_model=AvaliacaoDetalhe,
    status_code=status.HTTP_201_CREATED,
)
def avaliar_vaga(
    vaga_id: int,
    entrada: AvaliacaoEntrada,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> AvaliacaoDetalhe:
    """Registra a avaliação mútua (1 a 5 e comentário) dentro das 48h após a conclusão."""
    vaga = _buscar_vaga(db, vaga_id)
    registro = avaliacoes.registrar_avaliacao(
        db,
        vaga,
        usuario,
        nota=entrada.nota,
        comentario=entrada.comentario,
    )
    return avaliacoes.montar_detalhe(registro)


@router.get("/vagas/{vaga_id}/avaliacoes", response_model=AvaliacoesDaVaga)
def consultar_avaliacoes_da_vaga(
    vaga_id: int,
    usuario: Usuario | None = Depends(usuario_opcional),
    db: Session = Depends(get_db),
) -> AvaliacoesDaVaga:
    """Avaliações da vaga. A nota do outro lado fica oculta até ambos avaliarem ou o prazo."""
    vaga = _buscar_vaga(db, vaga_id)
    return avaliacoes.consultar_avaliacoes_da_vaga(db, vaga, usuario)


@router.get("/usuarios/{usuario_id}/avaliacoes", response_model=list[AvaliacaoDetalhe])
def listar_avaliacoes_do_usuario(
    usuario_id: int,
    db: Session = Depends(get_db),
) -> list[AvaliacaoDetalhe]:
    """Avaliações já visíveis recebidas pelo usuário."""
    _buscar_usuario(db, usuario_id)
    return avaliacoes.listar_avaliacoes_visiveis_do_usuario(db, usuario_id)


@router.get("/usuarios/{usuario_id}/reputacao", response_model=ReputacaoDetalhe)
def consultar_reputacao_do_usuario(
    usuario_id: int,
    db: Session = Depends(get_db),
) -> ReputacaoDetalhe:
    """Cálculo da reputação 70/20/10 e estado do selo Novo (até 5 serviços concluídos)."""
    usuario = _buscar_usuario(db, usuario_id)
    return avaliacoes.calcular_reputacao_usuario(db, usuario)
