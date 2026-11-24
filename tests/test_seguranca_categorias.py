"""Checklist de segurança ao publicar vaga e regra única de aptidão por categoria."""

import itertools
from datetime import timedelta

import pytest

from app.models import (
    ChecklistItem,
    EpiProfissional,
    Qualificacao,
    RiscoCategoria,
    StatusAprovacao,
    TipoPerfil,
    TipoQualificacao,
)
from app.services import match
from app.tempo import agora
from tests.fabricas import cabecalho, criar_categoria, criar_profissional, criar_usuario, criar_vaga


def _corpo(categoria, **extra):
    corpo = {
        "titulo": "Troca de fiação",
        "descricao": "Trocar a fiação da sala.",
        "categoria": categoria.slug,
        "valor": 200,
        "duracao_horas": 3,
        "data_inicio": (agora() + timedelta(hours=5)).isoformat(),
        "bairro": "Boa Viagem",
        "latitude": -8.12,
        "longitude": -34.9,
    }
    corpo.update(extra)
    return corpo


@pytest.fixture
def contratante(db):
    return criar_usuario(
        db, perfis=(TipoPerfil.CONTRATANTE,), perfil_ativo=TipoPerfil.CONTRATANTE, verificado=True
    )


def _categoria_com_checklist(db, risco):
    categoria = criar_categoria(db, risco=risco)
    critico = ChecklistItem(
        categoria_id=categoria.id, texto="Circuito desenergizado?", critico=True
    )
    comum = ChecklistItem(categoria_id=categoria.id, texto="Área organizada?", critico=False)
    db.add_all([critico, comum])
    db.commit()
    return categoria, critico, comum


def _publicar(cliente, contratante, categoria, **extra):
    return cliente.post(
        "/api/v1/vagas", json=_corpo(categoria, **extra), headers=cabecalho(contratante)
    )


# ---------------------------------------------------------------- checklist (SEG03)


def test_categoria_de_risco_exige_o_checklist_completo(cliente, db, contratante):
    categoria, critico, _ = _categoria_com_checklist(db, RiscoCategoria.ALTO)

    sem = _publicar(cliente, contratante, categoria)
    assert sem.status_code == 422
    assert sem.json()["detail"]["codigo"] == "checklist_incompleto"
    assert sem.json()["detail"]["itens_pendentes"] == [critico.id]


def test_item_critico_negativo_bloqueia_a_publicacao(cliente, db, contratante):
    categoria, critico, comum = _categoria_com_checklist(db, RiscoCategoria.MEDIO)

    resposta = _publicar(
        cliente,
        contratante,
        categoria,
        checklist=[
            {"item_id": critico.id, "atendido": False},
            {"item_id": comum.id, "atendido": True},
        ],
    )

    assert resposta.status_code == 422
    assert resposta.json()["detail"]["codigo"] == "checklist_critico_negativo"
    assert resposta.json()["detail"]["itens_nao_atendidos"] == [critico.id]


def test_checklist_com_criticos_atendidos_publica_e_item_comum_negativo_nao_bloqueia(
    cliente, db, contratante
):
    categoria, critico, comum = _categoria_com_checklist(db, RiscoCategoria.ALTO)

    resposta = _publicar(
        cliente,
        contratante,
        categoria,
        checklist=[
            {"item_id": critico.id, "atendido": True},
            {"item_id": comum.id, "atendido": False},
        ],
    )

    assert resposta.status_code == 201


def test_categoria_de_risco_baixo_dispensa_o_checklist(cliente, db, contratante):
    categoria, _, _ = _categoria_com_checklist(db, RiscoCategoria.BAIXO)
    assert _publicar(cliente, contratante, categoria).status_code == 201


def test_seguranca_da_categoria_devolve_os_ids_do_checklist(cliente, db, contratante):
    categoria, critico, _ = _categoria_com_checklist(db, RiscoCategoria.ALTO)

    itens = cliente.get(
        f"/api/v1/categorias/{categoria.slug}/seguranca", headers=cabecalho(contratante)
    ).json()["checklist"]

    assert {i["id"] for i in itens} >= {critico.id}


# ---------------------------------------------------------------- regra única de aptidão


@pytest.mark.parametrize(
    "risco, exige_qualificacao, exige_epi",
    list(itertools.product(list(RiscoCategoria), (False, True), (False, True))),
)
def test_pode_atuar_coincide_com_a_elegibilidade_do_match(db, risco, exige_qualificacao, exige_epi):
    categoria = criar_categoria(
        db, risco=risco, exige_qualificacao=exige_qualificacao, exige_epi=exige_epi
    )
    vaga = criar_vaga(db, categoria=categoria)
    cenarios = itertools.product(
        (StatusAprovacao.PENDENTE, StatusAprovacao.APROVADO),
        (False, True),
        (False, True),
    )
    perfis = []
    for experiencia, com_qualificacao, com_epi in cenarios:
        perfil = criar_profissional(db, categorias=(categoria,), experiencia_aprovada=experiencia)
        if com_qualificacao:
            db.add(
                Qualificacao(
                    profissional_id=perfil.id,
                    categoria_id=categoria.id,
                    tipo=TipoQualificacao.CERTIFICADO,
                    status=StatusAprovacao.APROVADO,
                )
            )
        if com_epi:
            db.add(
                EpiProfissional(
                    profissional_id=perfil.id,
                    categoria_id=categoria.id,
                    arquivo_url="epi.jpg",
                    status=StatusAprovacao.APROVADO,
                )
            )
        perfis.append(perfil)
    db.commit()

    elegiveis = {p.id for p in match.elegiveis(db, vaga)}

    for perfil in perfis:
        assert match.pode_atuar(db, perfil, categoria) == (perfil.id in elegiveis), perfil.id
