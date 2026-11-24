"""Disponibilidade do profissional: modo Disponível agora e raio de busca do mapa."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.vagas import profissional_ativo
from app.db import get_db
from app.models import Usuario
from app.services import configuracao, mapa
from app.services.mapa import MapaDeOfertas
from app.tempo import agora, como_utc

router = APIRouter(prefix="/profissional", tags=["profissional"])


class DisponibilidadeProfissional(BaseModel):
    disponivel_agora: bool
    disponivel_ate: datetime | None = None
    raio_km: int
    bloqueios: list[dict] = Field(default_factory=list)


class DisponivelAgoraEntrada(BaseModel):
    ativo: bool


class RaioEntrada(BaseModel):
    raio_km: int = Field(ge=1)


def _situacao(usuario: Usuario) -> DisponibilidadeProfissional:
    perfil = usuario.perfil_profissional
    ate = como_utc(perfil.disponivel_ate) if perfil.disponivel_ate else None
    ativo = ate is not None and ate > agora()
    return DisponibilidadeProfissional(
        disponivel_agora=ativo,
        disponivel_ate=ate if ativo else None,
        raio_km=perfil.raio_km,
    )


@router.get("/disponibilidade", response_model=DisponibilidadeProfissional)
def consultar_disponibilidade(
    usuario: Usuario = Depends(profissional_ativo),
) -> DisponibilidadeProfissional:
    """Situação atual do modo Disponível agora e do raio de busca."""
    return _situacao(usuario)


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
    return _situacao(usuario)


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
    return _situacao(usuario)


@router.get("/mapa-ofertas", response_model=MapaDeOfertas)
def mapa_de_ofertas(
    raio_km: int | None = Query(default=None, ge=1),
    usuario: Usuario = Depends(profissional_ativo),
    db: Session = Depends(get_db),
) -> MapaDeOfertas:
    """Ofertas abertas perto do profissional, filtradas pelas categorias e experiências dele."""
    return mapa.montar_mapa(db, usuario.perfil_profissional, raio_km)
