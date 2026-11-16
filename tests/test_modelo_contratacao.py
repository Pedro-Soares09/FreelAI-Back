from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    Categoria,
    Convite,
    Endereco,
    EstadoPagamento,
    EstadoVaga,
    Lancamento,
    Pagamento,
    PerfilContratante,
    PerfilProfissional,
    RiscoCategoria,
    TipoLancamento,
    TipoPessoa,
    Usuario,
    Vaga,
)
from app.tempo import agora


@pytest.fixture
def cenario(db):
    contratante_usuario = Usuario(nome="Bia", email="bia@example.com", senha_hash="hash")
    profissional_usuario = Usuario(nome="Caio", email="caio@example.com", senha_hash="hash")
    db.add_all([contratante_usuario, profissional_usuario])
    db.flush()
    contratante = PerfilContratante(
        usuario_id=contratante_usuario.id,
        tipo_pessoa=TipoPessoa.FISICA,
        documento="98765432100",
        telefone="81988880000",
    )
    profissional = PerfilProfissional(
        usuario_id=profissional_usuario.id,
        cpf="12345678909",
        data_nascimento=date(1990, 1, 1),
        telefone="81999990000",
    )
    categoria = Categoria(slug="diarista", nome="Diarista", risco=RiscoCategoria.BAIXO)
    endereco = Endereco(bairro="Boa Viagem", latitude=-8.12, longitude=-34.90)
    db.add_all([contratante, profissional, categoria, endereco])
    db.flush()
    vaga = Vaga(
        contratante_id=contratante.id,
        categoria_id=categoria.id,
        endereco_id=endereco.id,
        titulo="Limpeza pós-obra",
        descricao="Limpeza completa",
        inicio_em=agora() + timedelta(days=1),
        duracao_minutos=240,
        valor_inicial=Decimal("150.00"),
    )
    db.add(vaga)
    db.commit()
    return {"vaga": vaga, "profissional": profissional}


def test_vaga_nasce_publicada_com_valor_decimal(cenario):
    vaga = cenario["vaga"]

    assert vaga.estado == EstadoVaga.PUBLICADA
    assert vaga.valor_inicial == Decimal("150.00")
    assert vaga.valor_fixado is None


def test_profissional_nao_recebe_dois_convites_para_a_mesma_vaga(db, cenario):
    for posicao in (1, 2):
        db.add(
            Convite(
                vaga_id=cenario["vaga"].id,
                profissional_id=cenario["profissional"].id,
                posicao=posicao,
            )
        )
    with pytest.raises(IntegrityError):
        db.commit()


def test_vaga_tem_no_maximo_um_pagamento(db, cenario):
    vaga = cenario["vaga"]
    db.add(Pagamento(vaga_id=vaga.id, valor_total=Decimal("150.00")))
    db.commit()

    db.add(Pagamento(vaga_id=vaga.id, valor_total=Decimal("150.00")))
    with pytest.raises(IntegrityError):
        db.commit()


def test_lancamento_nao_pode_ser_alterado_nem_removido(db, cenario):
    pagamento = Pagamento(
        vaga_id=cenario["vaga"].id,
        valor_total=Decimal("150.00"),
        estado=EstadoPagamento.RETIDO,
    )
    db.add(pagamento)
    db.flush()
    lancamento = Lancamento(
        pagamento_id=pagamento.id, tipo=TipoLancamento.RETENCAO, valor=Decimal("150.00")
    )
    db.add(lancamento)
    db.commit()

    lancamento.valor = Decimal("1.00")
    with pytest.raises(RuntimeError):
        db.commit()
    db.rollback()

    db.delete(lancamento)
    with pytest.raises(RuntimeError):
        db.commit()
