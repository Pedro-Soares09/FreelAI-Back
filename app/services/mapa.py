"""Mapa de ofertas próximas: vagas abertas perto do profissional e compatíveis com o perfil.

Uma oferta é compatível quando a categoria da vaga está entre as categorias de atuação do
profissional ou entre as categorias das experiências que ele preencheu. Cada oferta informa se
ele já pode atuar nela (mesmas regras de segurança do match) e o que ainda falta aprovar.
"""

from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Categoria,
    Endereco,
    EstadoVaga,
    Experiencia,
    PerfilProfissional,
    Vaga,
)
from app.services import configuracao, match
from app.services.geo import arredondar_km, caixa_para_raio, distancia_km
from app.tempo import agora, como_utc

# Marco Zero, usado quando o profissional ainda não tem endereço.
CENTRO_PADRAO = (-8.0631, -34.8711)


class OfertaNoMapa(BaseModel):
    vaga_id: int
    titulo: str
    categoria_slug: str
    categoria_nome: str
    bairro: str
    latitude: float
    longitude: float
    distancia_km: float
    valor: Decimal
    inicio_em: str
    pode_atuar: bool = Field(description="Verdadeiro quando o perfil já cumpre as regras de risco.")
    pendencias: list[str] = Field(
        default_factory=list, description="O que falta aprovar: experiencia, qualificacao, epi."
    )


class MapaDeOfertas(BaseModel):
    centro_latitude: float
    centro_longitude: float
    raio_km: int
    categorias_do_perfil: list[str] = Field(
        description="Slugs das categorias de atuação e de experiência usadas no filtro."
    )
    ofertas: list[OfertaNoMapa]


def categorias_do_perfil(db: Session, perfil: PerfilProfissional) -> dict[int, Categoria]:
    """Categorias de atuação mais as das experiências preenchidas, por id."""
    ids = {categoria.id for categoria in perfil.categorias}
    ids.update(
        db.scalars(
            select(Experiencia.categoria_id).where(
                Experiencia.profissional_id == perfil.id, Experiencia.categoria_id.is_not(None)
            )
        ).all()
    )
    if not ids:
        return {}
    return {c.id: c for c in db.scalars(select(Categoria).where(Categoria.id.in_(ids))).all()}


def montar_mapa(
    db: Session, perfil: PerfilProfissional, raio_km: int | None = None
) -> MapaDeOfertas:
    maximo = int(configuracao.get("raio_mapa_km", db=db))
    raio = min(raio_km if raio_km is not None else perfil.raio_km, maximo)
    centro = (
        (perfil.endereco.latitude, perfil.endereco.longitude)
        if perfil.endereco is not None
        else CENTRO_PADRAO
    )
    categorias = categorias_do_perfil(db, perfil)
    ofertas: list[OfertaNoMapa] = []
    if categorias:
        caixa = caixa_para_raio(centro[0], centro[1], raio)
        consulta = (
            select(Vaga, Endereco)
            .join(Endereco, Endereco.id == Vaga.endereco_id)
            .where(
                Vaga.estado == EstadoVaga.PUBLICADA,
                Vaga.inicio_em > agora(),
                Vaga.categoria_id.in_(categorias),
                Endereco.latitude.between(caixa.lat_min, caixa.lat_max),
                Endereco.longitude.between(caixa.lon_min, caixa.lon_max),
            )
        )
        for vaga, endereco in db.execute(consulta).all():
            distancia = distancia_km(centro[0], centro[1], endereco.latitude, endereco.longitude)
            if distancia > raio:
                continue
            categoria = categorias[vaga.categoria_id]
            pendencias = match.pendencias_da_categoria(db, perfil, categoria)
            ofertas.append(
                OfertaNoMapa(
                    vaga_id=vaga.id,
                    titulo=vaga.titulo,
                    categoria_slug=categoria.slug,
                    categoria_nome=categoria.nome,
                    bairro=endereco.bairro,
                    # Oferta aberta: o pino é aproximado (2 casas decimais, cerca de 1 km).
                    latitude=round(endereco.latitude, 2),
                    longitude=round(endereco.longitude, 2),
                    distancia_km=arredondar_km(distancia),
                    valor=vaga.valor_fixado or vaga.valor_inicial,
                    inicio_em=como_utc(vaga.inicio_em).isoformat(),
                    pode_atuar=not pendencias,
                    pendencias=pendencias,
                )
            )
    ofertas.sort(key=lambda o: o.distancia_km)
    return MapaDeOfertas(
        centro_latitude=centro[0],
        centro_longitude=centro[1],
        raio_km=raio,
        categorias_do_perfil=sorted(c.slug for c in categorias.values()),
        ofertas=ofertas,
    )
