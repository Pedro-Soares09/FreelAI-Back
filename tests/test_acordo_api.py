import pytest

from app.config import settings
from app.models import Contrato, EstadoVaga, TipoPerfil, Vaga
from tests.fabricas import cabecalho, criar_cenario_vaga, criar_usuario


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    monkeypatch.setattr(settings, "perfil", "demo")


@pytest.fixture
def cenario(db):
    return criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)


def _url(cenario, sufixo=""):
    return f"/api/v1/vagas/{cenario.vaga.id}/acordo{sufixo}"


def test_rotas_exigem_token(cliente):
    assert cliente.get("/api/v1/vagas/1/acordo").status_code == 401
    assert cliente.post("/api/v1/vagas/1/acordo/aceite").status_code == 401


def test_acordo_traz_termos_e_divisao_de_valores(cliente, cenario):
    resposta = cliente.get(_url(cenario), headers=cabecalho(cenario.contratante))

    assert resposta.status_code == 200
    dados = resposta.json()
    assert dados["vaga_id"] == cenario.vaga.id
    assert dados["valor_total"] == "150.00"
    assert dados["taxa_plataforma"] == "15.00"
    assert dados["valor_liquido_profissional"] == "135.00"
    assert dados["aceites"] == {"profissional": False, "contratante": False}
    assert dados["profissional"]["nome"] == cenario.profissional.nome


def test_aceite_das_duas_partes_congela_o_contrato(cliente, db, cenario):
    primeiro = cliente.post(_url(cenario, "/aceite"), headers=cabecalho(cenario.profissional))
    segundo = cliente.post(_url(cenario, "/aceite"), headers=cabecalho(cenario.contratante))

    assert primeiro.json()["aceites"] == {"profissional": True, "contratante": False}
    assert segundo.json()["aceites"] == {"profissional": True, "contratante": True}
    contrato = db.query(Contrato).filter_by(vaga_id=cenario.vaga.id).one()
    assert contrato.versao_termos == "1.0"
    assert len(contrato.aceites) == 2


def test_aceite_repetido_nao_duplica(cliente, db, cenario):
    for _ in range(2):
        resposta = cliente.post(_url(cenario, "/aceite"), headers=cabecalho(cenario.contratante))
        assert resposta.status_code == 200

    assert len(db.query(Contrato).filter_by(vaga_id=cenario.vaga.id).one().aceites) == 1


def test_estranho_nao_ve_nem_aceita(cliente, db, cenario):
    intruso = criar_usuario(
        db, perfis=(TipoPerfil.CONTRATANTE,), perfil_ativo=TipoPerfil.CONTRATANTE, verificado=True
    )

    assert cliente.get(_url(cenario), headers=cabecalho(intruso)).status_code == 404
    assert cliente.post(_url(cenario, "/aceite"), headers=cabecalho(intruso)).status_code == 404


def test_vaga_cancelada_nao_aceita_o_acordo(cliente, db, cenario):
    vaga = db.get(Vaga, cenario.vaga.id)
    vaga.estado = EstadoVaga.CANCELADA
    db.commit()

    resposta = cliente.post(_url(cenario, "/aceite"), headers=cabecalho(cenario.contratante))

    assert resposta.status_code == 409
    assert resposta.json()["detail"]["codigo"] == "acordo_indisponivel"


def test_do_acordo_ao_pix_retido(cliente, db, cenario):
    cliente.post(_url(cenario, "/aceite"), headers=cabecalho(cenario.profissional))
    cliente.post(_url(cenario, "/aceite"), headers=cabecalho(cenario.contratante))

    cobranca = cliente.post(
        f"/api/v1/vagas/{cenario.vaga.id}/pagamento", headers=cabecalho(cenario.contratante)
    )
    assert cobranca.status_code == 201
    assert cobranca.json()["estado"] == "aguardando"
    assert cobranca.json()["codigo_pix"]

    pago = cliente.post(
        f"/api/v1/pagamentos/{cobranca.json()['id']}/simular-confirmacao",
        headers=cabecalho(cenario.contratante),
    )
    assert pago.status_code == 200
    assert pago.json()["estado"] == "retido"
    assert pago.json()["vaga_estado"] == "contratada"


def test_cobranca_exige_o_aceite_das_duas_partes(cliente, db, cenario):
    url = f"/api/v1/vagas/{cenario.vaga.id}/pagamento"
    sem_aceite = cliente.post(url, headers=cabecalho(cenario.contratante))
    assert sem_aceite.status_code == 409
    assert sem_aceite.json()["detail"]["codigo"] == "acordo_pendente"

    cliente.post(_url(cenario, "/aceite"), headers=cabecalho(cenario.contratante))
    so_um = cliente.post(url, headers=cabecalho(cenario.contratante))
    assert so_um.status_code == 409

    cliente.post(_url(cenario, "/aceite"), headers=cabecalho(cenario.profissional))
    assert cliente.post(url, headers=cabecalho(cenario.contratante)).status_code == 201


def test_acordo_continua_consultavel_em_disputa(cliente, db, cenario):
    vaga = db.get(Vaga, cenario.vaga.id)
    vaga.estado = EstadoVaga.EM_DISPUTA
    db.commit()

    consulta = cliente.get(_url(cenario), headers=cabecalho(cenario.contratante))
    aceite = cliente.post(_url(cenario, "/aceite"), headers=cabecalho(cenario.contratante))

    assert consulta.status_code == 200
    assert aceite.status_code == 409
