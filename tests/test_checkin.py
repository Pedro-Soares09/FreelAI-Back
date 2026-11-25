from datetime import UTC, datetime, timedelta

from app.config import settings
from app.models import EstadoVaga, TipoPerfil
from app.tempo import fixar_relogio
from tests.fabricas import cabecalho, criar_cenario_vaga, criar_usuario

BASE = datetime.now(UTC).replace(microsecond=0)
# Endereço do cenário de teste: Boa Viagem (latitude -8.12, longitude -34.90).
NO_LOCAL = {"latitude": -8.12, "longitude": -34.90}
LONGE = {"latitude": -8.05, "longitude": -34.95}


def _url(vaga_id: int) -> str:
    return f"/api/v1/vagas/{vaga_id}/checkin"


def _cenario(db, *, inicio_em):
    fixar_relogio(BASE)
    return criar_cenario_vaga(
        db, estado=EstadoVaga.CONTRATADA, inicio_em=inicio_em, aceites_completos=True
    )


def test_checkin_no_local_e_dentro_da_janela_e_registrado(cliente, db):
    cenario = _cenario(db, inicio_em=BASE + timedelta(minutes=10))

    resposta = cliente.post(
        _url(cenario.vaga.id), json=NO_LOCAL, headers=cabecalho(cenario.profissional)
    )

    assert resposta.status_code == 200
    assert resposta.json()["distancia_metros"] < 5
    db.refresh(cenario.vaga)
    assert cenario.vaga.checkin_em is not None
    detalhe = cliente.get(
        f"/api/v1/vagas/{cenario.vaga.id}", headers=cabecalho(cenario.profissional)
    )
    assert detalhe.json()["checkin_realizado_em"] is not None


def test_checkin_antes_da_janela_e_recusado(cliente, db):
    cenario = _cenario(db, inicio_em=BASE + timedelta(hours=3))

    resposta = cliente.post(
        _url(cenario.vaga.id), json=NO_LOCAL, headers=cabecalho(cenario.profissional)
    )

    assert resposta.status_code == 409
    assert resposta.json()["detail"]["codigo"] == "checkin_fora_da_janela"


def test_checkin_longe_do_local_e_recusado_com_a_distancia(cliente, db):
    cenario = _cenario(db, inicio_em=BASE + timedelta(minutes=10))

    resposta = cliente.post(
        _url(cenario.vaga.id), json=LONGE, headers=cabecalho(cenario.profissional)
    )

    assert resposta.status_code == 422
    assert resposta.json()["detail"]["codigo"] == "checkin_longe_do_local"
    assert resposta.json()["detail"]["distancia_metros"] > 300


def test_checkin_so_do_profissional_contratado(cliente, db):
    cenario = _cenario(db, inicio_em=BASE + timedelta(minutes=10))
    outro = criar_usuario(
        db, perfis=(TipoPerfil.PROFISSIONAL,), perfil_ativo=TipoPerfil.PROFISSIONAL
    )

    resposta = cliente.post(_url(cenario.vaga.id), json=NO_LOCAL, headers=cabecalho(outro))
    contratante = cliente.post(
        _url(cenario.vaga.id), json=NO_LOCAL, headers=cabecalho(cenario.contratante)
    )

    assert resposta.status_code == 403
    assert contratante.status_code == 403


def test_checkin_duplicado_e_recusado(cliente, db):
    cenario = _cenario(db, inicio_em=BASE + timedelta(minutes=10))
    cabecalhos = cabecalho(cenario.profissional)

    primeiro = cliente.post(_url(cenario.vaga.id), json=NO_LOCAL, headers=cabecalhos)
    segundo = cliente.post(_url(cenario.vaga.id), json=NO_LOCAL, headers=cabecalhos)

    assert primeiro.status_code == 200
    assert segundo.status_code == 409
    assert segundo.json()["detail"]["codigo"] == "checkin_ja_realizado"


def test_simular_local_so_vale_no_perfil_demo(cliente, db, monkeypatch):
    cenario = _cenario(db, inicio_em=BASE + timedelta(minutes=10))
    corpo = {**LONGE, "simular_local": True}

    sem_demo = cliente.post(
        _url(cenario.vaga.id), json=corpo, headers=cabecalho(cenario.profissional)
    )
    monkeypatch.setattr(settings, "perfil", "demo")
    com_demo = cliente.post(
        _url(cenario.vaga.id), json=corpo, headers=cabecalho(cenario.profissional)
    )

    assert sem_demo.status_code == 422
    assert com_demo.status_code == 200


def test_falta_nao_e_aceita_depois_do_checkin(cliente, db):
    cenario = _cenario(db, inicio_em=BASE - timedelta(minutes=45))
    cliente.post(_url(cenario.vaga.id), json=NO_LOCAL, headers=cabecalho(cenario.profissional))

    resposta = cliente.post(
        f"/api/v1/vagas/{cenario.vaga.id}/falta", headers=cabecalho(cenario.contratante)
    )

    assert resposta.status_code == 409
    assert resposta.json()["detail"]["codigo"] == "checkin_realizado"
