"""Rotas REST da central de notificações no app (polling, contagem e marcação de leitura) — US62."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.schemas.notificacoes import (
    CentralNotificacoes,
    ContagemNaoLidas,
    NotificacaoDetalhe,
    ResultadoLeituraTodas,
)
from app.db import get_db
from app.deps import usuario_atual
from app.models import Usuario
from app.services import notificacoes

router = APIRouter(prefix="/notificacoes", tags=["notificacoes"])


@router.get("", response_model=CentralNotificacoes)
def listar_notificacoes(
    apenas_nao_lidas: bool = Query(
        default=False,
        description="Quando verdadeiro, retorna apenas as notificações ainda não lidas.",
    ),
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> CentralNotificacoes:
    """Polling REST da central de notificações (exibe somente as cujo `visivel_em <= agora()`)."""
    return notificacoes.listar_notificacoes_visiveis(
        db,
        usuario.id,
        apenas_nao_lidas=apenas_nao_lidas,
    )


@router.get("/nao-lidas", response_model=ContagemNaoLidas)
def consultar_contagem_nao_lidas(
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> ContagemNaoLidas:
    """Quantidade de notificações já visíveis e ainda não lidas pelo usuário."""
    return ContagemNaoLidas(nao_lidas=notificacoes.contar_nao_lidas(db, usuario.id))


@router.post("/ler-todas", response_model=ResultadoLeituraTodas)
def marcar_todas_lidas(
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> ResultadoLeituraTodas:
    """Marca como lidas todas as notificações já visíveis do usuário."""
    return notificacoes.marcar_todas_como_lidas(db, usuario)


@router.post("/{notificacao_id}/lida", response_model=NotificacaoDetalhe)
def marcar_notificacao_como_lida(
    notificacao_id: int,
    usuario: Usuario = Depends(usuario_atual),
    db: Session = Depends(get_db),
) -> NotificacaoDetalhe:
    """Marca uma notificação visível como lida e registra `lida_em`."""
    return notificacoes.marcar_como_lida(db, notificacao_id, usuario)
