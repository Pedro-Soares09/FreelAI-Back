from datetime import UTC, datetime, timedelta

import pytest

from app.config import settings
from app.models import Convite, EstadoConvite, EstadoVaga, TipoPerfil, Vaga
from app.services import match
from app.tempo import avancar_relogio, fixar_relogio
from tests.fabricas import (
    cabecalho,
    criar_categoria,
    criar_profissional,
    criar_usuario,
)

BASE = datetime.now(UTC).replace(microsecond=0)


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    monkeypatch.setattr(settings, "perfil", "demo")
    fixar_relogio(BASE)


@pytest.fixture
def categoria(db):
    return criar_categoria(db)


@pytest.fixture
def contratante(db):
    return criar_usuario(
        db,
        perfis=(TipoPerfil.CONTRATANTE,),
        perfil_ativo=TipoPerfil.CONTRATANTE,
        verificado=True,
    )


def _corpo(categoria, **extra):
    corpo = {
        "titulo": "Montagem de guarda-roupa",
        "descricao": "Montar um guarda-roupa de seis portas",
        "categoria": categoria.slug,
        "valor": 150,
        "duracao_horas": 2,
        "data_inicio": (BASE + timedelta(days=1)).isoformat(),
        "bairro": "Várzea",
        "latitude": -8.0505,
        "longitude": -34.95,
    }
    corpo.update(extra)
    return corpo


def _publicar(cliente, contratante, categoria, **extra):
    resposta = cliente.post(
        "/api/v1/vagas", json=_corpo(categoria, **extra), headers=cabecalho(contratante)
    )
    assert resposta.status_code == 201, resposta.text
    return resposta.json()


def test_publicar_exige_perfil_contratante(cliente, db, categoria):
    profissional = criar_profissional(db, categorias=(categoria,))

    resposta = cliente.post(
        "/api/v1/vagas", json=_corpo(categoria), headers=cabecalho(profissional.usuario)
    )

    assert resposta.status_code == 403
    assert resposta.json()["detail"]["codigo"] == "perfil_ativo_incorreto"


def test_publicar_recusa_inicio_proximo_demais(cliente, contratante, categoria):
    resposta = cliente.post(
        "/api/v1/vagas",
        json=_corpo(categoria, data_inicio=BASE.isoformat()),
        headers=cabecalho(contratante),
    )

    assert resposta.status_code == 422
    assert resposta.json()["detail"]["codigo"] == "inicio_muito_proximo"


def test_publicar_gera_top3_para_o_contratante(cliente, db, contratante, categoria):
    for _ in range(4):
        criar_profissional(db, categorias=(categoria,))

    vaga = _publicar(cliente, contratante, categoria)
    top3 = cliente.get("/api/v1/contratante/top3", headers=cabecalho(contratante)).json()

    assert vaga["status"] == "publicada"
    assert len(top3) == 1
    assert top3[0]["vaga"]["id"] == vaga["id"]
    assert len(top3[0]["profissionais"]) == 3
    assert top3[0]["convite_atual"] is None


def test_fluxo_escolha_e_aceite(cliente, db, contratante, categoria):
    profissional = criar_profissional(db, categorias=(categoria,))
    vaga = _publicar(cliente, contratante, categoria)

    antes = cliente.get("/api/v1/profissional/convites", headers=cabecalho(profissional.usuario))
    assert antes.json() == []

    interesse = cliente.post(
        f"/api/v1/vagas/{vaga['id']}/interesse",
        json={"valor_proposto": 160},
        headers=cabecalho(profissional.usuario),
    )
    assert interesse.status_code == 200
    assert interesse.json()["status"] == "interessado"

    escolha = cliente.post(
        f"/api/v1/contratante/vagas/{vaga['id']}/escolher",
        json={"profissional_id": str(profissional.id)},
        headers=cabecalho(contratante),
    )
    assert escolha.status_code == 200
    assert escolha.json()["convite_atual"]["status"] == "em_andamento"
    assert escolha.json()["vaga"]["status"] == "em_contratacao"

    convite_id = cliente.get(
        "/api/v1/profissional/convites", headers=cabecalho(profissional.usuario)
    ).json()[0]["convite"]["id"]
    aceite = cliente.post(
        f"/api/v1/convites/{convite_id}/aceitar", headers=cabecalho(profissional.usuario)
    )

    assert aceite.status_code == 200
    assert aceite.json()["vaga_id"] == vaga["id"]
    db.expire_all()
    registro = db.get(Vaga, int(vaga["id"]))
    assert registro.estado == EstadoVaga.ACEITA
    assert registro.profissional_id == profissional.id
    assert float(registro.valor_fixado) == 160.0


def test_contraproposta_acima_do_limite_e_recusada(cliente, db, contratante, categoria):
    profissional = criar_profissional(db, categorias=(categoria,))
    vaga = _publicar(cliente, contratante, categoria)

    resposta = cliente.post(
        f"/api/v1/vagas/{vaga['id']}/interesse",
        json={"valor_proposto": 1000},
        headers=cabecalho(profissional.usuario),
    )

    assert resposta.status_code == 422
    assert resposta.json()["detail"]["codigo"] == "contraproposta_acima_do_limite"


def test_aceite_apos_o_prazo_e_barrado(cliente, db, contratante, categoria):
    profissional = criar_profissional(db, categorias=(categoria,))
    vaga = _publicar(cliente, contratante, categoria)
    cliente.post(
        f"/api/v1/contratante/vagas/{vaga['id']}/escolher",
        json={"profissional_id": str(profissional.id)},
        headers=cabecalho(contratante),
    )
    convite = db.query(Convite).filter_by(vaga_id=int(vaga["id"])).one()

    fixar_relogio(BASE + timedelta(hours=1))
    resposta = cliente.post(
        f"/api/v1/convites/{convite.id}/aceitar", headers=cabecalho(profissional.usuario)
    )

    assert resposta.status_code == 409
    assert resposta.json()["detail"]["codigo"] == "convite_expirado"


def test_recusar_convite_escolhido_reabre_a_vaga(cliente, db, contratante, categoria):
    profissional = criar_profissional(db, categorias=(categoria,))
    vaga = _publicar(cliente, contratante, categoria)
    cliente.post(
        f"/api/v1/contratante/vagas/{vaga['id']}/escolher",
        json={"profissional_id": str(profissional.id)},
        headers=cabecalho(contratante),
    )
    convite = db.query(Convite).filter_by(vaga_id=int(vaga["id"])).one()

    resposta = cliente.post(
        f"/api/v1/convites/{convite.id}/recusar", headers=cabecalho(profissional.usuario)
    )

    assert resposta.status_code == 204
    db.expire_all()
    assert db.get(Vaga, int(vaga["id"])).estado == EstadoVaga.PUBLICADA
    assert db.get(Convite, convite.id).estado == EstadoConvite.RECUSADO


def test_nao_escolhe_profissional_fora_da_rodada(cliente, db, contratante, categoria):
    criar_profissional(db, categorias=(categoria,))
    fora = criar_profissional(db, categorias=())
    vaga = _publicar(cliente, contratante, categoria)

    resposta = cliente.post(
        f"/api/v1/contratante/vagas/{vaga['id']}/escolher",
        json={"profissional_id": str(fora.id)},
        headers=cabecalho(contratante),
    )

    assert resposta.status_code == 409


def test_outro_contratante_nao_escolhe(cliente, db, contratante, categoria):
    profissional = criar_profissional(db, categorias=(categoria,))
    vaga = _publicar(cliente, contratante, categoria)
    intruso = criar_usuario(
        db, perfis=(TipoPerfil.CONTRATANTE,), perfil_ativo=TipoPerfil.CONTRATANTE, verificado=True
    )

    resposta = cliente.post(
        f"/api/v1/contratante/vagas/{vaga['id']}/escolher",
        json={"profissional_id": str(profissional.id)},
        headers=cabecalho(intruso),
    )

    assert resposta.status_code == 403


def test_oportunidades_respeitam_o_raio_e_a_categoria(cliente, db, contratante, categoria):
    perto = criar_profissional(db, categorias=(categoria,))
    _publicar(cliente, contratante, categoria)
    _publicar(
        cliente, contratante, categoria, latitude=-8.3, longitude=-35.2, titulo="Longe demais"
    )

    lista = cliente.get(
        "/api/v1/vagas/oportunidades?raio_km=5", headers=cabecalho(perto.usuario)
    ).json()
    outra = cliente.get(
        "/api/v1/vagas/oportunidades?raio_km=5&categoria=inexistente",
        headers=cabecalho(perto.usuario),
    ).json()

    assert [v["titulo"] for v in lista] == ["Montagem de guarda-roupa"]
    assert outra == []


def test_detalhe_oculta_endereco_completo_de_quem_nao_e_parte(cliente, db, contratante, categoria):
    profissional = criar_profissional(db, categorias=(categoria,))
    vaga = _publicar(cliente, contratante, categoria, logradouro="Rua do Sol")

    dono = cliente.get(f"/api/v1/vagas/{vaga['id']}", headers=cabecalho(contratante)).json()
    visitante = cliente.get(
        f"/api/v1/vagas/{vaga['id']}", headers=cabecalho(profissional.usuario)
    ).json()

    assert "Rua do Sol" in dono["endereco_completo"]
    assert visitante["endereco_completo"] is None
    assert visitante["meu_convite"]["status"] == "pendente_interesse"


def test_rotas_exigem_token(cliente):
    assert cliente.get("/api/v1/vagas/1").status_code == 401
    assert cliente.get("/api/v1/contratante/top3").status_code == 401
    assert cliente.get("/api/v1/categorias").status_code == 401


def test_interesse_fora_do_top3_devolve_403(cliente, db, contratante, categoria):
    vaga = _publicar(cliente, contratante, categoria)
    de_fora = criar_profissional(db, categorias=())

    resposta = cliente.post(
        f"/api/v1/vagas/{vaga['id']}/interesse",
        json={"valor_proposto": 160},
        headers=cabecalho(de_fora.usuario),
    )

    assert resposta.status_code == 403
    assert resposta.json()["detail"]["codigo"] == "fora_do_top3"


def test_segunda_contraproposta_e_barrada(cliente, db, contratante, categoria):
    profissional = criar_profissional(db, categorias=(categoria,))
    vaga = _publicar(cliente, contratante, categoria)
    url = f"/api/v1/vagas/{vaga['id']}/interesse"

    primeira = cliente.post(
        url, json={"valor_proposto": 160}, headers=cabecalho(profissional.usuario)
    )
    segunda = cliente.post(
        url, json={"valor_proposto": 170}, headers=cabecalho(profissional.usuario)
    )

    assert primeira.status_code == 200
    assert segunda.status_code == 409
    assert segunda.json()["detail"]["codigo"] == "interesse_ja_registrado"


def test_varredura_expira_convite_escolhido_vencido(cliente, db, contratante, categoria):
    profissional = criar_profissional(db, categorias=(categoria,))
    vaga = _publicar(cliente, contratante, categoria)
    cliente.post(
        f"/api/v1/vagas/{vaga['id']}/interesse",
        json={},
        headers=cabecalho(profissional.usuario),
    )
    cliente.post(
        f"/api/v1/contratante/vagas/{vaga['id']}/escolher",
        json={"profissional_id": str(profissional.id)},
        headers=cabecalho(contratante),
    )
    assert match.expirar_convites_vencidos(db) == 0

    avancar_relogio(timedelta(hours=2))
    assert match.expirar_convites_vencidos(db) == 1

    db.expire_all()
    assert db.get(Vaga, int(vaga["id"])).estado == EstadoVaga.PUBLICADA
    convites = cliente.get("/api/v1/profissional/convites", headers=cabecalho(profissional.usuario))
    assert convites.json() == []
