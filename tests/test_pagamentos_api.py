from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.config import settings
from app.models import EstadoConta, EstadoPagamento, EstadoVaga, Pagamento, TipoPerfil
from app.services import pagamentos
from app.tempo import fixar_relogio
from tests.fabricas import cabecalho, criar_cenario_vaga, criar_usuario

BASE = datetime.now(UTC).replace(microsecond=0)


def _url_pagamento(vaga_id: int) -> str:
    return f"/api/v1/vagas/{vaga_id}/pagamento"


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setattr(settings, "perfil", "demo")


@pytest.fixture
def cenario(db):
    fixar_relogio(BASE)
    return criar_cenario_vaga(
        db, aceites_completos=True, inicio_em=BASE + timedelta(hours=1), duracao_minutos=120
    )


# Autorização


def test_rotas_exigem_token(cliente, cenario):
    vaga_id = cenario.vaga.id

    assert cliente.post(_url_pagamento(vaga_id)).status_code == 401
    assert cliente.get(_url_pagamento(vaga_id)).status_code == 401
    assert cliente.post(f"/api/v1/vagas/{vaga_id}/conclusao").status_code == 401
    assert cliente.post(f"/api/v1/vagas/{vaga_id}/validacao-facial").status_code == 401
    assert cliente.post("/api/v1/pagamentos/1/simular-confirmacao").status_code == 401


def test_profissional_nao_abre_cobranca(cliente, cenario):
    resposta = cliente.post(
        _url_pagamento(cenario.vaga.id), headers=cabecalho(cenario.profissional)
    )

    assert resposta.status_code == 403
    assert resposta.json()["detail"]["codigo"] == "perfil_ativo_incorreto"


def test_outro_contratante_nao_abre_nem_consulta_a_cobranca(cliente, db, cenario):
    outro = criar_usuario(db, perfis=(TipoPerfil.CONTRATANTE,), perfil_ativo=TipoPerfil.CONTRATANTE)

    abrir = cliente.post(_url_pagamento(cenario.vaga.id), headers=cabecalho(outro))
    cliente.post(_url_pagamento(cenario.vaga.id), headers=cabecalho(cenario.contratante))
    consultar = cliente.get(_url_pagamento(cenario.vaga.id), headers=cabecalho(outro))

    assert abrir.status_code == 403
    assert abrir.json()["detail"]["codigo"] == "vaga_de_outro_contratante"
    assert consultar.status_code == 403
    assert consultar.json()["detail"]["codigo"] == "sem_acesso_ao_pagamento"


def test_conta_pendente_nao_abre_cobranca(cliente, db):
    pendente = criar_usuario(
        db,
        estado=EstadoConta.PENDENTE,
        perfis=(TipoPerfil.CONTRATANTE,),
        perfil_ativo=TipoPerfil.CONTRATANTE,
    )
    cenario = criar_cenario_vaga(db, aceites_completos=True, contratante=pendente)

    resposta = cliente.post(_url_pagamento(cenario.vaga.id), headers=cabecalho(pendente))

    assert resposta.status_code == 403
    assert resposta.json()["detail"]["codigo"] == "conta_pendente"


def test_profissional_contratado_consulta_o_pagamento(cliente, cenario):
    cliente.post(_url_pagamento(cenario.vaga.id), headers=cabecalho(cenario.contratante))

    resposta = cliente.get(_url_pagamento(cenario.vaga.id), headers=cabecalho(cenario.profissional))

    assert resposta.status_code == 200
    assert resposta.json()["vaga_id"] == cenario.vaga.id


def test_vaga_inexistente_e_pagamento_inexistente_devolvem_404(cliente, cenario):
    cabecalhos = cabecalho(cenario.contratante)

    assert cliente.post(_url_pagamento(9999), headers=cabecalhos).status_code == 404
    sem_cobranca = cliente.get(_url_pagamento(cenario.vaga.id), headers=cabecalhos)
    assert sem_cobranca.status_code == 404
    assert sem_cobranca.json()["detail"]["codigo"] == "pagamento_nao_encontrado"


# Abertura da cobrança


def test_abrir_cobranca_devolve_divisao_e_codigo_pix(cliente, cenario):
    resposta = cliente.post(_url_pagamento(cenario.vaga.id), headers=cabecalho(cenario.contratante))

    assert resposta.status_code == 201
    corpo = resposta.json()
    assert corpo["estado"] == "aguardando"
    assert corpo["vaga_estado"] == "aguardando_pagamento"
    assert corpo["metodo"] == "pix"
    assert corpo["codigo_pix"].startswith("PIX-SIM-")
    assert corpo["divisao"] == {
        "valor_total": "150.00",
        "percentual_taxa": "10",
        "taxa_plataforma": "15.00",
        "valor_liquido_profissional": "135.00",
    }
    assert corpo["saldo_retido"] == "0.00"
    assert corpo["lancamentos"] == []


def test_abrir_cobranca_duas_vezes_gera_409(cliente, cenario):
    cabecalhos = cabecalho(cenario.contratante)
    cliente.post(_url_pagamento(cenario.vaga.id), headers=cabecalhos)

    resposta = cliente.post(_url_pagamento(cenario.vaga.id), headers=cabecalhos)

    assert resposta.status_code == 409
    assert resposta.json()["detail"]["codigo"] == "pagamento_ja_existe"


# Simulação da confirmação


def test_simular_confirmacao_nao_existe_em_producao(cliente, cenario, monkeypatch):
    monkeypatch.setattr(settings, "perfil", "producao")
    cabecalhos = cabecalho(cenario.contratante)
    pagamento_id = cliente.post(_url_pagamento(cenario.vaga.id), headers=cabecalhos).json()["id"]

    resposta = cliente.post(
        f"/api/v1/pagamentos/{pagamento_id}/simular-confirmacao", headers=cabecalhos
    )

    assert resposta.status_code == 404


def test_simular_confirmacao_retem_o_valor_em_demo(cliente, cenario, demo):
    cabecalhos = cabecalho(cenario.contratante)
    pagamento_id = cliente.post(_url_pagamento(cenario.vaga.id), headers=cabecalhos).json()["id"]

    resposta = cliente.post(
        f"/api/v1/pagamentos/{pagamento_id}/simular-confirmacao", headers=cabecalhos
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["estado"] == "retido"
    assert corpo["vaga_estado"] == "contratada"
    assert corpo["saldo_retido"] == "150.00"
    assert [item["tipo"] for item in corpo["lancamentos"]] == ["retencao"]


def test_simular_confirmacao_recusa_quem_nao_e_o_contratante(cliente, cenario, demo):
    pagamento_id = cliente.post(
        _url_pagamento(cenario.vaga.id), headers=cabecalho(cenario.contratante)
    ).json()["id"]

    resposta = cliente.post(
        f"/api/v1/pagamentos/{pagamento_id}/simular-confirmacao",
        headers=cabecalho(cenario.profissional),
    )

    assert resposta.status_code == 403


# Fluxo completo


def test_fluxo_completo_ate_a_liberacao_integral(cliente, db, cenario, demo):
    cabecalhos = cabecalho(cenario.contratante)
    vaga_id = cenario.vaga.id

    criado = cliente.post(_url_pagamento(vaga_id), headers=cabecalhos)
    assert criado.status_code == 201
    pagamento_id = criado.json()["id"]

    retido = cliente.post(
        f"/api/v1/pagamentos/{pagamento_id}/simular-confirmacao", headers=cabecalhos
    )
    assert retido.status_code == 200
    assert retido.json()["estado"] == "retido"

    cedo = cliente.post(f"/api/v1/vagas/{vaga_id}/conclusao", headers=cabecalhos)
    assert cedo.status_code == 409
    assert cedo.json()["detail"]["codigo"] == "servico_nao_terminou"

    facial_cedo = cliente.post(f"/api/v1/vagas/{vaga_id}/validacao-facial", headers=cabecalhos)
    assert facial_cedo.status_code == 409
    assert facial_cedo.json()["detail"]["codigo"] == "vaga_nao_concluida"

    fixar_relogio(pagamentos.fim_do_servico(cenario.vaga) + timedelta(minutes=5))
    concluida = cliente.post(f"/api/v1/vagas/{vaga_id}/conclusao", headers=cabecalhos)
    assert concluida.status_code == 200
    assert concluida.json()["vaga_estado"] == "concluida"
    assert concluida.json()["estado"] == "retido"

    liberado = cliente.post(
        f"/api/v1/vagas/{vaga_id}/validacao-facial",
        json={"selfie_url": "https://arquivos.exemplo/selfies/1.jpg"},
        headers=cabecalhos,
    )
    assert liberado.status_code == 200
    corpo = liberado.json()
    assert corpo["estado"] == "liberado"
    assert corpo["vaga_estado"] == "liberada"
    assert corpo["saldo_retido"] == "0.00"
    assert [(item["tipo"], item["valor"]) for item in corpo["lancamentos"]] == [
        ("retencao", "150.00"),
        ("liberacao_profissional", "135.00"),
        ("taxa_plataforma", "15.00"),
    ]

    consulta = cliente.get(_url_pagamento(vaga_id), headers=cabecalho(cenario.profissional))
    assert consulta.status_code == 200
    assert consulta.json() == corpo

    pagamento = db.get(Pagamento, pagamento_id)
    db.refresh(pagamento)
    assert pagamento.estado == EstadoPagamento.LIBERADO
    assert pagamentos.saldo_retido(pagamento) == Decimal("0.00")
    db.refresh(cenario.vaga)
    assert cenario.vaga.estado == EstadoVaga.LIBERADA


def test_validacao_facial_repetida_devolve_409_sem_novos_lancamentos(cliente, cenario, demo):
    cabecalhos = cabecalho(cenario.contratante)
    vaga_id = cenario.vaga.id
    pagamento_id = cliente.post(_url_pagamento(vaga_id), headers=cabecalhos).json()["id"]
    cliente.post(f"/api/v1/pagamentos/{pagamento_id}/simular-confirmacao", headers=cabecalhos)
    fixar_relogio(pagamentos.fim_do_servico(cenario.vaga))
    cliente.post(f"/api/v1/vagas/{vaga_id}/conclusao", headers=cabecalhos)
    primeira = cliente.post(f"/api/v1/vagas/{vaga_id}/validacao-facial", headers=cabecalhos)

    segunda = cliente.post(f"/api/v1/vagas/{vaga_id}/validacao-facial", headers=cabecalhos)

    assert primeira.status_code == 200
    assert segunda.status_code == 409
    consulta = cliente.get(_url_pagamento(vaga_id), headers=cabecalhos).json()
    assert len(consulta["lancamentos"]) == 3


def test_contrato_inclui_as_rotas_de_pagamento(cliente):
    caminhos = cliente.get("/openapi.json").json()["paths"]

    assert "/api/v1/vagas/{vaga_id}/pagamento" in caminhos
    assert "/api/v1/vagas/{vaga_id}/conclusao" in caminhos
    assert "/api/v1/vagas/{vaga_id}/validacao-facial" in caminhos
    assert "/api/v1/pagamentos/{pagamento_id}/simular-confirmacao" in caminhos
    assert "/api/v1/vagas/{vaga_id}/acordo" in caminhos
