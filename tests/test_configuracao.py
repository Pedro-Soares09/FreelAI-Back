import pytest
from sqlalchemy import select

from app.models import Configuracao, PapelUsuario
from app.services import configuracao, varreduras
from app.services.configuracao import PARAMETROS, ChaveDesconhecida
from tests.fabricas import cabecalho, criar_usuario


def test_padroes_de_producao_usam_prazos_reais():
    assert configuracao.get("janela_aceite_min", perfil="producao") == 30
    assert configuracao.get("prazo_pagamento_min", perfil="producao") == 15
    assert configuracao.get("liberacao_parcial_min", perfil="producao") == 72 * 60
    assert configuracao.get("raio_padrao_km", perfil="producao") == 5
    assert configuracao.get("raio_maximo_km", perfil="producao") == 15


def test_perfil_demo_reduz_os_prazos_para_minutos():
    assert configuracao.get("janela_aceite_min", perfil="demo") <= 5
    assert configuracao.get("liberacao_parcial_min", perfil="demo") <= 10
    for chave, parametro in PARAMETROS.items():
        if chave.endswith("_min"):
            assert parametro.demo <= parametro.producao, chave


def test_pesos_do_match_somam_um():
    for perfil in ("producao", "demo"):
        soma = sum(configuracao.get(c, perfil=perfil) for c in PARAMETROS if c.startswith("peso_"))
        assert soma == pytest.approx(1.0)


def test_chave_desconhecida_levanta_erro():
    with pytest.raises(ChaveDesconhecida):
        configuracao.get("nao_existe")


def test_perfil_ativo_vem_da_variavel_de_ambiente(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "perfil", "demo")
    assert configuracao.get("janela_aceite_min") == configuracao.get(
        "janela_aceite_min", perfil="demo"
    )


def test_semear_cria_os_dois_perfis_e_e_idempotente(db):
    criadas = configuracao.semear_configuracoes(db)
    assert criadas == len(PARAMETROS) * 2

    assert configuracao.semear_configuracoes(db) == 0
    total = len(db.scalars(select(Configuracao)).all())
    assert total == len(PARAMETROS) * 2


def test_linha_do_banco_sobrepoe_o_padrao(db):
    configuracao.semear_configuracoes(db)
    linha = db.scalar(
        select(Configuracao).where(
            Configuracao.chave == "janela_aceite_min", Configuracao.perfil == "producao"
        )
    )
    linha.valor = "45"
    db.commit()

    assert configuracao.get("janela_aceite_min", db=db, perfil="producao") == 45
    assert configuracao.get("janela_aceite_min", perfil="producao") == 30


def test_varredura_forcada_exige_admin(cliente, db):
    comum = criar_usuario(db)

    resposta = cliente.post("/api/v1/admin/prazos/varrer", headers=cabecalho(comum))

    assert resposta.status_code == 403
    assert cliente.post("/api/v1/admin/prazos/varrer").status_code == 401


def test_varredura_forcada_executa_as_varreduras_registradas(cliente, db):
    admin = criar_usuario(db, papel=PapelUsuario.ADMIN)
    chamadas = []

    @varreduras.varredura("teste_expiracao")
    def varrer_teste(sessao):
        chamadas.append(sessao)
        return 3

    try:
        resposta = cliente.post("/api/v1/admin/prazos/varrer", headers=cabecalho(admin))
    finally:
        varreduras.remover_varredura("teste_expiracao")

    assert resposta.status_code == 200
    assert resposta.json()["alteracoes"]["teste_expiracao"] == 3
    assert len(chamadas) == 1


def test_endpoint_lista_configuracoes_efetivas(cliente, db):
    admin = criar_usuario(db, papel=PapelUsuario.ADMIN)

    resposta = cliente.get("/api/v1/admin/configuracoes", headers=cabecalho(admin))

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["perfil"] in {"producao", "demo"}
    assert set(corpo["valores"]) == set(PARAMETROS)
