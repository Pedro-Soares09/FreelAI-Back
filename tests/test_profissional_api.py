from datetime import timedelta

from app.tempo import avancar_relogio
from tests.fabricas import cabecalho, criar_profissional


def test_disponibilidade_inicial_e_modo_disponivel_agora(cliente, db):
    profissional = criar_profissional(db)
    cab = cabecalho(profissional.usuario)

    inicial = cliente.get("/api/v1/profissional/disponibilidade", headers=cab)
    assert inicial.status_code == 200
    assert inicial.json()["disponivel_agora"] is False

    ligado = cliente.post(
        "/api/v1/profissional/disponivel-agora", json={"ativo": True}, headers=cab
    )
    assert ligado.json()["disponivel_agora"] is True
    assert ligado.json()["disponivel_ate"] is not None

    avancar_relogio(timedelta(hours=5))
    assert (
        cliente.get("/api/v1/profissional/disponibilidade", headers=cab).json()["disponivel_agora"]
        is False
    )

    cliente.post("/api/v1/profissional/disponivel-agora", json={"ativo": True}, headers=cab)
    desligado = cliente.post(
        "/api/v1/profissional/disponivel-agora", json={"ativo": False}, headers=cab
    )
    assert desligado.json()["disponivel_agora"] is False


def test_raio_e_salvo_e_limitado_ao_maximo(cliente, db):
    profissional = criar_profissional(db)
    cab = cabecalho(profissional.usuario)

    salvo = cliente.patch("/api/v1/profissional/raio", json={"raio_km": 8}, headers=cab)
    assert salvo.json()["raio_km"] == 8

    limitado = cliente.patch("/api/v1/profissional/raio", json={"raio_km": 500}, headers=cab)
    assert limitado.json()["raio_km"] == 15
    assert cliente.get("/api/v1/profissional/disponibilidade", headers=cab).json()["raio_km"] == 15


def test_contratante_nao_acessa_disponibilidade(cliente, db):
    from app.models import TipoPerfil
    from tests.fabricas import criar_usuario

    contratante = criar_usuario(
        db, perfis=(TipoPerfil.CONTRATANTE,), perfil_ativo=TipoPerfil.CONTRATANTE, verificado=True
    )
    resposta = cliente.get("/api/v1/profissional/disponibilidade", headers=cabecalho(contratante))
    assert resposta.status_code == 403
