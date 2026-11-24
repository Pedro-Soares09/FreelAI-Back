"""Cadastro dos perfis, verificação de identidade, comprovações e filas de aprovação."""

from datetime import date, timedelta
from io import BytesIO

import pytest

from app.api.v1.schemas.cadastro import cnpj_valido, cpf_valido
from app.models import (
    EpiCategoria,
    EstadoConta,
    PapelUsuario,
    PerfilProfissional,
    RiscoCategoria,
    StatusAprovacao,
    Usuario,
)
from tests.fabricas import cabecalho, criar_categoria, criar_usuario

BYTES_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
CPF = "52998224725"
CPF_OUTRO = "11144477735"
CNPJ = "11222333000181"


def _conta_pendente(db):
    return criar_usuario(db, estado=EstadoConta.PENDENTE)


def _admin(db):
    return criar_usuario(db, papel=PapelUsuario.ADMIN, verificado=True)


def _corpo_profissional(base, /, **extra):
    corpo = {
        "cpf": "529.982.247-25",
        "data_nascimento": str(date.today() - timedelta(days=365 * 30)),
        "telefone": "(81) 99999-1234",
        "bairro": "Boa Viagem",
        "latitude": -8.12,
        "longitude": -34.9,
        "raio_km": 8,
        "categorias": [c.slug for c in base],
        "caracteristicas": {"comunicacao": 5, "agilidade": 4},
        "experiencias": [{"descricao": "Cinco anos montando móveis.", "arquivo_url": None}],
    }
    corpo.update(extra)
    return corpo


def _enviar_arquivo(cliente, usuario, tipo):
    resposta = cliente.post(
        "/api/v1/arquivos",
        data={"tipo": tipo},
        files={"arquivo": ("arquivo.jpg", BytesIO(BYTES_JPEG), "image/jpeg")},
        headers=cabecalho(usuario),
    )
    assert resposta.status_code == 201
    return resposta.json()["id"]


# ---------------------------------------------------------------- validadores


def test_validadores_de_cpf_e_cnpj():
    assert cpf_valido(CPF)
    assert not cpf_valido("11111111111")
    assert not cpf_valido("52998224724")
    assert cnpj_valido(CNPJ)
    assert not cnpj_valido("11222333000180")


# ---------------------------------------------------------------- cadastro profissional


def test_cadastra_profissional_com_localizacao_categorias_e_experiencia(cliente, db):
    categoria = criar_categoria(db)
    conta = _conta_pendente(db)

    resposta = cliente.post(
        "/api/v1/perfis/profissional",
        json=_corpo_profissional([categoria]),
        headers=cabecalho(conta),
    )

    assert resposta.status_code == 201
    dados = resposta.json()
    assert dados["cpf"] == CPF
    assert dados["telefone"] == "81999991234"
    assert dados["bairro"] == "Boa Viagem"
    assert dados["raio_km"] == 8
    assert dados["verificacao"] == "pendente"
    assert dados["categorias"][0]["pendencias"] == []
    assert dados["caracteristicas"]["comunicacao"] == 5
    me = cliente.get("/api/v1/auth/me", headers=cabecalho(conta)).json()
    assert me["perfis"] == ["profissional"]
    assert me["perfil_ativo"] == "profissional"


@pytest.mark.parametrize(
    "alteracao",
    [
        {"cpf": "11111111111"},
        {"data_nascimento": str(date.today() - timedelta(days=365 * 16))},
        {"telefone": "123"},
        {"categorias": []},
        {"latitude": 200},
    ],
)
def test_cadastro_profissional_recusa_dados_invalidos(cliente, db, alteracao):
    categoria = criar_categoria(db)
    resposta = cliente.post(
        "/api/v1/perfis/profissional",
        json=_corpo_profissional([categoria], **alteracao),
        headers=cabecalho(_conta_pendente(db)),
    )
    assert resposta.status_code == 422


def test_cadastro_profissional_recusa_categoria_inexistente_cpf_e_perfil_repetidos(cliente, db):
    categoria = criar_categoria(db)
    conta = _conta_pendente(db)

    inexistente = cliente.post(
        "/api/v1/perfis/profissional",
        json=_corpo_profissional([categoria], categorias=["nao-existe"]),
        headers=cabecalho(conta),
    )
    assert inexistente.status_code == 422
    assert inexistente.json()["detail"]["codigo"] == "categoria_inexistente"

    corpo = _corpo_profissional([categoria])
    assert (
        cliente.post(
            "/api/v1/perfis/profissional", json=corpo, headers=cabecalho(conta)
        ).status_code
        == 201
    )
    repetido = cliente.post("/api/v1/perfis/profissional", json=corpo, headers=cabecalho(conta))
    assert repetido.status_code == 409
    assert repetido.json()["detail"]["codigo"] == "perfil_ja_cadastrado"

    outra = cliente.post(
        "/api/v1/perfis/profissional", json=corpo, headers=cabecalho(_conta_pendente(db))
    )
    assert outra.status_code == 409
    assert outra.json()["detail"]["codigo"] == "cpf_em_uso"


def test_atualiza_raio_limitado_e_categorias(cliente, db):
    uma, outra = criar_categoria(db), criar_categoria(db)
    conta = _conta_pendente(db)
    cliente.post(
        "/api/v1/perfis/profissional", json=_corpo_profissional([uma]), headers=cabecalho(conta)
    )

    resposta = cliente.patch(
        "/api/v1/perfis/profissional/meu",
        json={"raio_km": 500, "categorias": [outra.slug], "telefone": "81 3333-4444"},
        headers=cabecalho(conta),
    )

    assert resposta.status_code == 200
    assert resposta.json()["raio_km"] == 15
    assert [c["slug"] for c in resposta.json()["categorias"]] == [outra.slug]
    assert resposta.json()["telefone"] == "8133334444"


# ---------------------------------------------------------------- cadastro contratante


def test_cadastra_contratante_pf_e_pj_sem_cnpj(cliente, db):
    pf = cliente.post(
        "/api/v1/perfis/contratante",
        json={"tipo_pessoa": "pf", "documento": CPF, "telefone": "81988887777"},
        headers=cabecalho(_conta_pendente(db)),
    )
    pj = cliente.post(
        "/api/v1/perfis/contratante",
        json={"tipo_pessoa": "pj", "razao_social": "Buffet Ltda", "telefone": "8133334444"},
        headers=cabecalho(_conta_pendente(db)),
    )
    pj_cnpj = cliente.post(
        "/api/v1/perfis/contratante",
        json={"tipo_pessoa": "pj", "documento": CNPJ, "telefone": "8133334444"},
        headers=cabecalho(_conta_pendente(db)),
    )

    assert pf.status_code == 201 and pj.status_code == 201 and pj_cnpj.status_code == 201
    assert pj.json()["documento"] is None
    assert pf.json()["verificacao"] == "pendente"


def test_contratante_pf_exige_cpf_valido_e_pj_recusa_cnpj_invalido(cliente, db):
    sem_cpf = cliente.post(
        "/api/v1/perfis/contratante",
        json={"tipo_pessoa": "pf", "telefone": "81988887777"},
        headers=cabecalho(_conta_pendente(db)),
    )
    cnpj_ruim = cliente.post(
        "/api/v1/perfis/contratante",
        json={"tipo_pessoa": "pj", "documento": "123", "telefone": "81988887777"},
        headers=cabecalho(_conta_pendente(db)),
    )
    assert sem_cpf.status_code == 422
    assert cnpj_ruim.status_code == 422


# ---------------------------------------------------------------- verificação de identidade


def _cadastrar_profissional(cliente, db, categorias=None):
    categorias = categorias or [criar_categoria(db)]
    conta = _conta_pendente(db)
    cliente.post(
        "/api/v1/perfis/profissional",
        json=_corpo_profissional(categorias),
        headers=cabecalho(conta),
    )
    return conta


def test_fluxo_completo_da_verificacao_ate_a_ativacao_da_conta(cliente, db):
    conta = _cadastrar_profissional(cliente, db)
    admin = _admin(db)

    inicial = cliente.get("/api/v1/verificacao/status", headers=cabecalho(conta)).json()
    assert inicial["status"] == "pendente"
    assert inicial["consentimento_biometria"] is False

    documento = _enviar_arquivo(cliente, conta, "documento")
    selfie = _enviar_arquivo(cliente, conta, "selfie")
    corpo = {"documento_arquivo_id": documento, "selfie_arquivo_id": selfie}
    sem_consentimento = cliente.post(
        "/api/v1/verificacao/envio", json=corpo, headers=cabecalho(conta)
    )
    assert sem_consentimento.status_code == 409
    assert sem_consentimento.json()["detail"]["codigo"] == "consentimento_pendente"

    consentido = cliente.post("/api/v1/verificacao/consentimento", headers=cabecalho(conta))
    assert consentido.json()["consentimento_biometria"] is True
    assert (
        cliente.post("/api/v1/verificacao/consentimento", headers=cabecalho(conta)).status_code
        == 200
    )

    enviado = cliente.post("/api/v1/verificacao/envio", json=corpo, headers=cabecalho(conta))
    assert enviado.status_code == 200
    assert enviado.json()["status"] == "em_analise"
    repetido = cliente.post("/api/v1/verificacao/envio", json=corpo, headers=cabecalho(conta))
    assert repetido.status_code == 409

    fila = cliente.get(
        "/api/v1/admin/aprovacoes?tipo=profissional", headers=cabecalho(admin)
    ).json()
    assert len(fila) == 1
    item = fila[0]
    assert {a["tipo"] for a in item["arquivos"]} == {"documento", "selfie"}

    decisao = cliente.post(
        f"/api/v1/admin/aprovacoes/profissional/{item['id']}/decisao",
        json={"decisao": "aprovar"},
        headers=cabecalho(admin),
    )
    assert decisao.status_code == 200
    assert decisao.json()["status"] == "aprovado"
    db.expire_all()
    assert db.get(Usuario, conta.id).estado == EstadoConta.ATIVA
    final = cliente.get("/api/v1/verificacao/status", headers=cabecalho(conta)).json()
    assert final["status"] == "verificado"
    assert cliente.get("/api/v1/auth/me", headers=cabecalho(conta)).json()["verificado"] is True


def test_recusa_exige_motivo_e_permite_reenvio(cliente, db):
    conta = _cadastrar_profissional(cliente, db)
    admin = _admin(db)
    cliente.post("/api/v1/verificacao/consentimento", headers=cabecalho(conta))
    corpo = {
        "documento_arquivo_id": _enviar_arquivo(cliente, conta, "documento"),
        "selfie_arquivo_id": _enviar_arquivo(cliente, conta, "selfie"),
    }
    cliente.post("/api/v1/verificacao/envio", json=corpo, headers=cabecalho(conta))
    perfil_id = db.query(PerfilProfissional).filter_by(usuario_id=conta.id).one().id
    url = f"/api/v1/admin/aprovacoes/profissional/{perfil_id}/decisao"

    sem_motivo = cliente.post(url, json={"decisao": "recusar"}, headers=cabecalho(admin))
    assert sem_motivo.status_code == 422

    recusada = cliente.post(
        url, json={"decisao": "recusar", "motivo": "Documento ilegível."}, headers=cabecalho(admin)
    )
    assert recusada.status_code == 200
    status_ = cliente.get("/api/v1/verificacao/status", headers=cabecalho(conta)).json()
    assert status_["status"] == "recusado"
    assert status_["motivo"] == "Documento ilegível."

    novo = {
        "documento_arquivo_id": _enviar_arquivo(cliente, conta, "documento"),
        "selfie_arquivo_id": _enviar_arquivo(cliente, conta, "selfie"),
    }
    reenvio = cliente.post("/api/v1/verificacao/envio", json=novo, headers=cabecalho(conta))
    assert reenvio.status_code == 200
    assert reenvio.json()["status"] == "em_analise"
    assert reenvio.json()["motivo"] is None


def test_envio_recusa_arquivo_de_outro_usuario_ou_de_tipo_errado(cliente, db):
    conta = _cadastrar_profissional(cliente, db)
    outro = _conta_pendente(db)
    cliente.post("/api/v1/verificacao/consentimento", headers=cabecalho(conta))
    foto = _enviar_arquivo(cliente, conta, "foto")
    selfie_alheia = _enviar_arquivo(cliente, outro, "selfie")

    resposta = cliente.post(
        "/api/v1/verificacao/envio",
        json={"documento_arquivo_id": foto, "selfie_arquivo_id": selfie_alheia},
        headers=cabecalho(conta),
    )

    assert resposta.status_code == 422
    assert resposta.json()["detail"]["codigo"] == "arquivo_invalido"


def test_verificacao_do_contratante_ativa_a_conta(cliente, db):
    conta = _conta_pendente(db)
    admin = _admin(db)
    cliente.post(
        "/api/v1/perfis/contratante",
        json={"tipo_pessoa": "pf", "documento": CPF, "telefone": "81988887777"},
        headers=cabecalho(conta),
    )
    cliente.post("/api/v1/verificacao/consentimento", headers=cabecalho(conta))
    cliente.post(
        "/api/v1/verificacao/envio",
        json={
            "documento_arquivo_id": _enviar_arquivo(cliente, conta, "documento"),
            "selfie_arquivo_id": _enviar_arquivo(cliente, conta, "selfie"),
        },
        headers=cabecalho(conta),
    )
    fila = cliente.get("/api/v1/admin/aprovacoes?tipo=contratante", headers=cabecalho(admin)).json()

    cliente.post(
        f"/api/v1/admin/aprovacoes/contratante/{fila[0]['id']}/decisao",
        json={"decisao": "aprovar"},
        headers=cabecalho(admin),
    )

    db.expire_all()
    assert db.get(Usuario, conta.id).estado == EstadoConta.ATIVA


def test_filas_do_admin_sao_restritas(cliente, db):
    comum = criar_usuario(db, verificado=True)
    assert cliente.get("/api/v1/admin/aprovacoes", headers=cabecalho(comum)).status_code == 403
    assert (
        cliente.post(
            "/api/v1/admin/aprovacoes/epi/1/decisao",
            json={"decisao": "aprovar"},
            headers=cabecalho(comum),
        ).status_code
        == 403
    )


# ---------------------------------------------------------------- comprovações


def test_categoria_de_risco_mostra_o_que_falta_e_a_aprovacao_libera(cliente, db):
    categoria = criar_categoria(
        db, risco=RiscoCategoria.ALTO, exige_qualificacao=True, exige_epi=True
    )
    conta = _cadastrar_profissional(cliente, db, [categoria])
    admin = _admin(db)

    antes = cliente.get("/api/v1/perfis/profissional/meu", headers=cabecalho(conta)).json()
    assert set(antes["categorias"][0]["pendencias"]) == {"experiencia", "qualificacao", "epi"}

    qualificacao = cliente.post(
        "/api/v1/profissional/qualificacoes",
        json={"categoria": categoria.slug, "tipo": "certificado", "referencia": "CREA 123"},
        headers=cabecalho(conta),
    )
    epi = cliente.post(
        "/api/v1/profissional/epis",
        json={"categoria": categoria.slug, "arquivo_url": "/api/v1/arquivos/epi/x.jpg"},
        headers=cabecalho(conta),
    )
    assert qualificacao.status_code == 201 and epi.status_code == 201

    pendentes = cliente.get("/api/v1/admin/aprovacoes", headers=cabecalho(admin)).json()
    por_tipo = {i["tipo"]: i for i in pendentes}
    assert {"experiencia", "qualificacao", "epi"} <= set(por_tipo)
    for tipo, item in por_tipo.items():
        if tipo in ("experiencia", "qualificacao", "epi"):
            resposta = cliente.post(
                f"/api/v1/admin/aprovacoes/{tipo}/{item['id']}/decisao",
                json={"decisao": "aprovar"},
                headers=cabecalho(admin),
            )
            assert resposta.status_code == 200

    depois = cliente.get("/api/v1/perfis/profissional/meu", headers=cabecalho(conta)).json()
    assert depois["experiencia_aprovada"] == "aprovado"
    assert depois["categorias"][0]["pendencias"] == []
    comprovacoes = cliente.get("/api/v1/profissional/comprovacoes", headers=cabecalho(conta)).json()
    assert {c["status"] for c in comprovacoes} == {"aprovado"}


def test_recusa_de_experiencia_mantem_a_pendencia(cliente, db):
    categoria = criar_categoria(db, risco=RiscoCategoria.MEDIO)
    conta = _cadastrar_profissional(cliente, db, [categoria])
    admin = _admin(db)
    item = cliente.get(
        "/api/v1/admin/aprovacoes?tipo=experiencia", headers=cabecalho(admin)
    ).json()[0]

    cliente.post(
        f"/api/v1/admin/aprovacoes/experiencia/{item['id']}/decisao",
        json={"decisao": "recusar", "motivo": "Sem comprovação."},
        headers=cabecalho(admin),
    )

    perfil = cliente.get("/api/v1/perfis/profissional/meu", headers=cabecalho(conta)).json()
    assert perfil["experiencia_aprovada"] == "recusado"
    assert "experiencia" in perfil["categorias"][0]["pendencias"]


def test_seguranca_da_categoria_lista_epis_e_checklist(cliente, db):
    categoria = criar_categoria(db, risco=RiscoCategoria.ALTO, exige_epi=True)
    db.add(EpiCategoria(categoria_id=categoria.id, descricao="Luvas isolantes"))
    db.commit()
    conta = criar_usuario(db)

    resposta = cliente.get(
        f"/api/v1/categorias/{categoria.slug}/seguranca", headers=cabecalho(conta)
    )
    ausente = cliente.get("/api/v1/categorias/nao-existe/seguranca", headers=cabecalho(conta))

    assert resposta.status_code == 200
    assert resposta.json()["epis"] == ["Luvas isolantes"]
    assert resposta.json()["exige_epi"] is True
    assert ausente.status_code == 404


def test_aprovar_item_inexistente_ou_ja_decidido_devolve_404(cliente, db):
    admin = _admin(db)
    resposta = cliente.post(
        "/api/v1/admin/aprovacoes/epi/999/decisao",
        json={"decisao": "aprovar"},
        headers=cabecalho(admin),
    )
    assert resposta.status_code == 404
    assert StatusAprovacao.PENDENTE.value == "pendente"
