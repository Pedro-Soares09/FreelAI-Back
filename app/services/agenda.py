"""Agenda do profissional: períodos bloqueados em que ele não recebe convites."""

from datetime import datetime

from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erros import erro_http
from app.models import BloqueioAgenda, PerfilProfissional
from app.tempo import agora, como_utc


def listar(db: Session, profissional: PerfilProfissional, *, so_futuros: bool = True):
    consulta = select(BloqueioAgenda).where(BloqueioAgenda.profissional_id == profissional.id)
    if so_futuros:
        consulta = consulta.where(BloqueioAgenda.fim_em > agora())
    return list(db.scalars(consulta.order_by(BloqueioAgenda.inicio_em)))


def criar(
    db: Session,
    profissional: PerfilProfissional,
    inicio: datetime,
    fim: datetime,
    motivo: str | None,
) -> BloqueioAgenda:
    inicio, fim = como_utc(inicio), como_utc(fim)
    if fim <= inicio:
        raise erro_http(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "periodo_invalido",
            "O fim do bloqueio precisa ser depois do início.",
        )
    if fim <= agora():
        raise erro_http(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "periodo_no_passado",
            "O bloqueio precisa terminar no futuro.",
        )
    for existente in listar(db, profissional):
        if como_utc(existente.inicio_em) < fim and como_utc(existente.fim_em) > inicio:
            raise erro_http(
                status.HTTP_409_CONFLICT,
                "bloqueio_sobreposto",
                "Já existe um bloqueio neste período.",
                bloqueio_id=str(existente.id),
            )
    bloqueio = BloqueioAgenda(
        profissional_id=profissional.id,
        inicio_em=inicio,
        fim_em=fim,
        motivo=motivo.strip()[:120] if motivo and motivo.strip() else None,
    )
    db.add(bloqueio)
    db.commit()
    db.refresh(bloqueio)
    return bloqueio


def remover(db: Session, profissional: PerfilProfissional, bloqueio_id: int) -> None:
    bloqueio = db.get(BloqueioAgenda, bloqueio_id)
    if bloqueio is None or bloqueio.profissional_id != profissional.id:
        raise erro_http(
            status.HTTP_404_NOT_FOUND, "bloqueio_nao_encontrado", "Bloqueio não encontrado."
        )
    db.delete(bloqueio)
    db.commit()


def profissionais_bloqueados(
    db: Session, ids: list[int], inicio: datetime, fim: datetime
) -> set[int]:
    """Profissionais com bloqueio que se sobrepõe ao intervalo informado."""
    if not ids:
        return set()
    bloqueios = db.scalars(
        select(BloqueioAgenda).where(
            BloqueioAgenda.profissional_id.in_(ids),
            BloqueioAgenda.inicio_em < fim,
            BloqueioAgenda.fim_em > inicio,
        )
    )
    return {b.profissional_id for b in bloqueios}
