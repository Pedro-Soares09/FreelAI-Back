from sqlalchemy import func, select

from app.models import (
    Categoria,
    Endereco,
    PerfilContratante,
    PerfilProfissional,
    Usuario,
    Vaga,
    categorias_profissional,
)
from app.seed import semear
from app.services.geo import distancia_km


def _quantidade(db, modelo) -> int:
    return db.scalar(select(func.count()).select_from(modelo))


def test_seed_cria_quantidades_e_e_idempotente(db):
    semear(db)
    contagens = {
        modelo: _quantidade(db, modelo)
        for modelo in (Categoria, Usuario, PerfilProfissional, PerfilContratante, Vaga)
    }

    assert contagens == {
        Categoria: 5,
        Usuario: 26,  # 1 admin + 20 profissionais + 5 contratantes
        PerfilProfissional: 20,
        PerfilContratante: 5,
        Vaga: 6,
    }

    semear(db)
    assert {modelo: _quantidade(db, modelo) for modelo in contagens} == contagens


def test_cada_categoria_tem_profissional_novo_e_pro(db):
    semear(db)
    categorias = db.scalars(select(Categoria)).all()

    for categoria in categorias:
        profissionais = db.scalars(
            select(PerfilProfissional)
            .join(
                categorias_profissional,
                categorias_profissional.c.profissional_id == PerfilProfissional.id,
            )
            .where(categorias_profissional.c.categoria_id == categoria.id)
        ).all()
        assert any(p.servicos_concluidos < 5 for p in profissionais), categoria.slug
        assert any(p.pro for p in profissionais), categoria.slug


def test_cada_vaga_tem_profissionais_dentro_e_fora_de_cinco_km(db):
    semear(db)
    vagas = db.scalars(select(Vaga)).all()
    profissionais = db.scalars(select(PerfilProfissional)).all()

    for vaga in vagas:
        endereco_vaga = db.get(Endereco, vaga.endereco_id)
        distancias = []
        for profissional in profissionais:
            endereco_profissional = db.get(Endereco, profissional.endereco_id)
            distancias.append(
                distancia_km(
                    endereco_vaga.latitude,
                    endereco_vaga.longitude,
                    endereco_profissional.latitude,
                    endereco_profissional.longitude,
                )
            )
        assert any(distancia <= 5 for distancia in distancias), vaga.titulo
        assert any(distancia > 5 for distancia in distancias), vaga.titulo
