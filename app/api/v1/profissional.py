"""Disponibilidade do profissional: modo Disponível agora e raio de busca do mapa."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.vagas import profissional_ativo
from app.db import get_db
from app.models import Usuario
from app.services import agenda, configuracao, mapa
from app.services.mapa import MapaDeOfertas
from app.tempo import agora, como_utc

router = APIRouter(prefix="/profissional", tags=["profissional"])


class DisponibilidadeProfissional(BaseModel):
    disponivel_agora: bool
    disponivel_ate: datetime | None = None
    raio_km: int
    bloqueios: list["BloqueioDaAgenda"] = Field(default_factory=list)


class BloqueioDaAgenda(BaseModel):
    id: str
    inicio: datetime
    fim: datetime
    motivo: str | None = None


class BloqueioEntrada(BaseModel):
    inicio: datetime
    fim: datetime
    motivo: str | None = Field(default=None, max_length=120)


def _bloqueio(registro) -> BloqueioDaAgenda:
    return BloqueioDaAgenda(
        id=str(registro.id),
        inicio=como_utc(registro.inicio_em),
        fim=como_utc(registro.fim_em),
        motivo=registro.motivo,
    )


class DisponivelAgoraEntrada(BaseModel):
    ativo: bool


class RaioEntrada(BaseModel):
    raio_km: int = Field(ge=1)


def _situacao(usuario: Usuario, db: Session) -> DisponibilidadeProfissional:
    perfil = usuario.perfil_profissional
    ate = como_utc(perfil.disponivel_ate) if perfil.disponivel_ate else None
    ativo = ate is not None and ate > agora()
    return DisponibilidadeProfissional(
        disponivel_agora=ativo,
        disponivel_ate=ate if ativo else None,
        raio_km=perfil.raio_km,
        bloqueios=[_bloqueio(b) for b in agenda.listar(db, perfil)],
    )


@router.get("/disponibilidade", response_model=DisponibilidadeProfissional)
def consultar_disponibilidade(
    usuario: Usuario = Depends(profissional_ativo),
    db: Session = Depends(get_db),
) -> DisponibilidadeProfissional:
    """Situação atual do modo Disponível agora, do raio de busca e da agenda."""
    return _situacao(usuario, db)


@router.post("/disponivel-agora", response_model=DisponibilidadeProfissional)
def alternar_disponivel_agora(
    entrada: DisponivelAgoraEntrada,
    usuario: Usuario = Depends(profissional_ativo),
    db: Session = Depends(get_db),
) -> DisponibilidadeProfissional:
    """Liga o modo Disponível agora pela duração configurada, ou desliga."""
    perfil = usuario.perfil_profissional
    if entrada.ativo:
        minutos = configuracao.get("disponivel_agora_min", db=db)
        perfil.disponivel_ate = agora() + timedelta(minutes=minutos)
    else:
        perfil.disponivel_ate = None
    db.commit()
    db.refresh(perfil)
    return _situacao(usuario, db)


@router.patch("/raio", response_model=DisponibilidadeProfissional)
def atualizar_raio(
    entrada: RaioEntrada,
    usuario: Usuario = Depends(profissional_ativo),
    db: Session = Depends(get_db),
) -> DisponibilidadeProfissional:
    """Salva o raio de busca do mapa, limitado ao máximo configurado."""
    perfil = usuario.perfil_profissional
    perfil.raio_km = min(entrada.raio_km, configuracao.get("raio_maximo_km", db=db))
    db.commit()
    db.refresh(perfil)
    return _situacao(usuario, db)


@router.get("/mapa-ofertas", response_model=MapaDeOfertas)
def mapa_de_ofertas(
    raio_km: int | None = Query(default=None, ge=1),
    usuario: Usuario = Depends(profissional_ativo),
    db: Session = Depends(get_db),
) -> MapaDeOfertas:
    """Ofertas abertas perto do profissional, filtradas pelas categorias e experiências dele."""
    return mapa.montar_mapa(db, usuario.perfil_profissional, raio_km)


@router.get("/agenda/bloqueios", response_model=list[BloqueioDaAgenda])
def listar_bloqueios(
    usuario: Usuario = Depends(profissional_ativo), db: Session = Depends(get_db)
) -> list[BloqueioDaAgenda]:
    """Períodos bloqueados que ainda não terminaram."""
    return [_bloqueio(b) for b in agenda.listar(db, usuario.perfil_profissional)]


@router.post(
    "/agenda/bloqueios", response_model=BloqueioDaAgenda, status_code=status.HTTP_201_CREATED
)
def criar_bloqueio(
    entrada: BloqueioEntrada,
    usuario: Usuario = Depends(profissional_ativo),
    db: Session = Depends(get_db),
) -> BloqueioDaAgenda:
    """Bloqueia um período: o profissional não recebe convites que o atravessem."""
    registro = agenda.criar(
        db, usuario.perfil_profissional, entrada.inicio, entrada.fim, entrada.motivo
    )
    return _bloqueio(registro)


@router.delete("/agenda/bloqueios/{bloqueio_id}", status_code=status.HTTP_204_NO_CONTENT)
def remover_bloqueio(
    bloqueio_id: int,
    usuario: Usuario = Depends(profissional_ativo),
    db: Session = Depends(get_db),
) -> Response:
    agenda.remover(db, usuario.perfil_profissional, bloqueio_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
