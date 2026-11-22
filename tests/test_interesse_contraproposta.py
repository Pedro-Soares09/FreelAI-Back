from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    Categoria,
    Convite,
    Endereco,
    PerfilContratante,
    PerfilProfissional,
    RiscoCategoria,
    TipoPerfil,
    Usuario,
    Vaga,
)
from app.models.enums import EstadoConvite
from app.services.match import manifestar_interesse


def _montar_cenario():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    u_cli = Usuario(
        nome="Cliente Teste",
        email="cli@test.com",
        senha_hash="x",
        perfil_ativo=TipoPerfil.CONTRATANTE,
    )
    u_pro = Usuario(
        nome="Profissional Teste",
        email="pro@test.com",
        senha_hash="x",
        perfil_ativo=TipoPerfil.PROFISSIONAL,
    )
    db.add_all([u_cli, u_pro])
    db.flush()

    cli = PerfilContratante(
        usuario_id=u_cli.id,
        tipo_pessoa="pf",
        telefone="81999999999",
    )
    pro = PerfilProfissional(
        usuario_id=u_pro.id,
        cpf="12345678901",
        data_nascimento=date(1998, 2, 21),
        telefone="81988888888",
        raio_km=10,
    )
    cat = Categoria(
        slug="servicos-gerais",
        nome="Servicos Gerais",
        risco=RiscoCategoria.BAIXO,
    )
    end = Endereco(
        logradouro="Rua Teste",
        numero="10",
        bairro="Afogados",
        cidade="Recife",
        uf="PE",
        latitude=-8.07,
        longitude=-34.90,
    )
    db.add_all([cli, pro, cat, end])
    db.flush()

    vaga_top1 = Vaga(
        contratante_id=cli.id,
        categoria_id=cat.id,
        endereco_id=end.id,
        titulo="Vaga Top 1",
        descricao="Descricao da vaga top 1",
        inicio_em=datetime(2026, 12, 1, 9, 0),
        duracao_minutos=60,
        valor_inicial=Decimal("100.00"),
    )
    vaga_fora = Vaga(
        contratante_id=cli.id,
        categoria_id=cat.id,
        endereco_id=end.id,
        titulo="Vaga Fora Top 3",
        descricao="Descricao da vaga fora top 3",
        inicio_em=datetime(2026, 12, 2, 9, 0),
        duracao_minutos=60,
        valor_inicial=Decimal("100.00"),
    )
    db.add_all([vaga_top1, vaga_fora])
    db.flush()

    convite_top1 = Convite(vaga_id=vaga_top1.id, profissional_id=pro.id, posicao=1)
    convite_fora = Convite(vaga_id=vaga_fora.id, profissional_id=pro.id, posicao=4)
    db.add_all([convite_top1, convite_fora])
    db.commit()
    return db, u_pro, convite_top1, convite_fora


def test_profissional_fora_do_top3_recebe_403():
    db, u_pro, _, convite_fora = _montar_cenario()

    with pytest.raises(HTTPException) as exc:
        manifestar_interesse(db, convite_fora.id, u_pro)

    assert exc.value.status_code == 403


def test_contraproposta_acima_de_50_pct_e_recusada():
    db, u_pro, convite_top1, _ = _montar_cenario()

    with pytest.raises(HTTPException) as exc:
        manifestar_interesse(db, convite_top1.id, u_pro, Decimal("150.01"))

    assert exc.value.status_code == 422


def test_segunda_contraproposta_retorna_409():
    db, u_pro, convite_top1, _ = _montar_cenario()

    convite = manifestar_interesse(db, convite_top1.id, u_pro, Decimal("140.00"))
    assert convite.estado == EstadoConvite.INTERESSADO
    assert convite.valor_proposto == Decimal("140.00")

    with pytest.raises(HTTPException) as exc:
        manifestar_interesse(db, convite_top1.id, u_pro, Decimal("145.00"))

    assert exc.value.status_code == 409


def test_interesse_sem_contraproposta_mantem_valor_proposto_nulo():
    db, u_pro, convite_top1, _ = _montar_cenario()

    convite = manifestar_interesse(db, convite_top1.id, u_pro)
    assert convite.estado == EstadoConvite.INTERESSADO
    assert convite.valor_proposto is None
