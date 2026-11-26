"""Correções finais da revisão técnica: disputa, cadastro, rate limit, convites, privacidade."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.config import settings
from app.models import EstadoPagamento, EstadoVaga
from app.tempo import fixar_relogio
from tests.fabricas import cabecalho, criar_cenario_vaga

BASE = datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setattr(settings, "perfil", "demo")


def _confirmar_pix(db, cobranca):
    """Confirma o Pix pelo serviço (a rota de simulação só existe no perfil demo)."""
    from app.models import Pagamento
    from app.services import pagamentos

    pagamentos.reter(db, db.get(Pagamento, cobranca["id"]))


def _concluida_com_cobranca(cliente, db):
    fixar_relogio(BASE)
    cenario = criar_cenario_vaga(
        db,
        aceites_completos=True,
        estado=EstadoVaga.CONTRATADA,
        inicio_em=BASE - timedelta(hours=3),
        duracao_minutos=120,
        valor=Decimal("200.00"),
    )
    cobranca = cliente.post(
        f"/api/v1/vagas/{cenario.vaga.id}/conclusao", headers=cabecalho(cenario.contratante)
    ).json()
    return cenario, cobranca


# ---------------------------------------------------------------- 1. disputa e Pix confirmado


def test_pix_confirmado_com_disputa_aberta_nao_tira_a_vaga_da_disputa(cliente, db):
    """CT-DIS-01: a confirmação do Pix não rebaixa a vaga em disputa nem libera o valor."""
    cenario, cobranca = _concluida_com_cobranca(cliente, db)
    aberta = cliente.post(
        f"/api/v1/vagas/{cenario.vaga.id}/disputa",
        json={"descricao": "O serviço não foi feito como combinado."},
        headers=cabecalho(cenario.contratante),
    )
    assert aberta.status_code == 201, aberta.text

    _confirmar_pix(db, cobranca)

    db.refresh(cenario.vaga)
    assert cenario.vaga.estado == EstadoVaga.EM_DISPUTA
    pagamento = cliente.get(
        f"/api/v1/vagas/{cenario.vaga.id}/pagamento", headers=cabecalho(cenario.contratante)
    ).json()
    assert pagamento["estado"] != EstadoPagamento.LIBERADO.value


def test_falta_e_cancelamento_nao_escapam_da_disputa(cliente, db):
    """CT-DIS-02: com a vaga em disputa, falta e cancelamento são recusados."""
    cenario, cobranca = _concluida_com_cobranca(cliente, db)
    cliente.post(
        f"/api/v1/vagas/{cenario.vaga.id}/disputa",
        json={"descricao": "O serviço não foi feito como combinado."},
        headers=cabecalho(cenario.contratante),
    )
    _confirmar_pix(db, cobranca)

    falta = cliente.post(
        f"/api/v1/vagas/{cenario.vaga.id}/falta", headers=cabecalho(cenario.contratante)
    )
    cancelamento = cliente.post(
        f"/api/v1/vagas/{cenario.vaga.id}/cancelamento",
        json={"motivo": "Desisti do serviço."},
        headers=cabecalho(cenario.contratante),
    )

    assert falta.status_code == 409
    assert cancelamento.status_code == 409
    db.refresh(cenario.vaga)
    assert cenario.vaga.estado == EstadoVaga.EM_DISPUTA


# ---------------------------------------------------------------- 2. identidade verificada


def _profissional_verificado(db, verificado=True):
    from app.models import TipoPerfil
    from tests.fabricas import criar_usuario

    return criar_usuario(
        db,
        perfis=(TipoPerfil.PROFISSIONAL,),
        perfil_ativo=TipoPerfil.PROFISSIONAL,
        verificado=verificado,
    )


@pytest.mark.parametrize(
    "campo",
    [{"nome": "Outro Nome"}, {"cpf": "52998224725"}, {"data_nascimento": "1985-05-05"}],
    ids=["nome", "cpf", "nascimento"],
)
def test_identidade_nao_muda_depois_da_verificacao(cliente, db, campo):
    """CT-CAD-01: nome, CPF e nascimento ficam bloqueados para o profissional verificado."""
    profissional = _profissional_verificado(db)
    perfil = profissional.perfil_profissional
    perfil.cpf = "11144477735"
    db.commit()

    resposta = cliente.patch(
        "/api/v1/perfis/profissional/meu", json=campo, headers=cabecalho(profissional)
    )

    assert resposta.status_code == 409
    assert resposta.json()["detail"]["codigo"] == "identidade_verificada"


def test_verificado_ainda_edita_telefone_e_reenvia_os_mesmos_dados(cliente, db):
    """CT-CAD-01: telefone muda e o reenvio dos mesmos valores de identidade é aceito."""
    profissional = _profissional_verificado(db)

    resposta = cliente.patch(
        "/api/v1/perfis/profissional/meu",
        json={"telefone": "81988887777", "nome": profissional.nome},
        headers=cabecalho(profissional),
    )

    assert resposta.status_code == 200, resposta.text


def test_nao_verificado_ainda_pode_corrigir_a_identidade(cliente, db):
    """CT-CAD-01: antes da aprovação a identidade continua editável."""
    profissional = _profissional_verificado(db, verificado=False)

    resposta = cliente.patch(
        "/api/v1/perfis/profissional/meu",
        json={"nome": "Nome Corrigido"},
        headers=cabecalho(profissional),
    )

    assert resposta.status_code == 200, resposta.text


# ---------------------------------------------------------------- 4. convites sem escolha e prazo


def _convites_antigos(db, estados, minutos_atras):
    from sqlalchemy import select

    from app.models import Convite
    from app.tempo import agora
    from tests.fabricas import criar_categoria
    from tests.test_cancelamento_sancoes_rodadas_agenda import _vaga_com_rodada

    categoria = criar_categoria(db)
    vaga = _vaga_com_rodada(db, categoria, estados)
    for convite in db.scalars(select(Convite).where(Convite.vaga_id == vaga.id)):
        convite.criado_em = agora() - timedelta(minutes=minutos_atras)
    db.commit()
    return vaga, categoria


def test_convites_sem_escolha_expiram_e_a_proxima_rodada_abre(db):
    """CT-RODADA-01: convites sugeridos ou com interesse vencem e destravam a nova rodada."""
    from sqlalchemy import select

    from app.models import Convite, EstadoConvite
    from app.services import varreduras
    from tests.fabricas import criar_profissional

    vaga, categoria = _convites_antigos(
        db, [EstadoConvite.SUGERIDO, EstadoConvite.INTERESSADO, EstadoConvite.SUGERIDO], 24 * 60
    )
    novos = [criar_profissional(db, categorias=(categoria,)) for _ in range(3)]

    resultado = varreduras.executar_varreduras(db)

    db.refresh(vaga)
    assert resultado["alteracoes"]["expiracao_convites_sem_resposta"] == 3
    assert vaga.rodada == 2
    segunda = db.scalars(
        select(Convite.profissional_id).where(Convite.vaga_id == vaga.id, Convite.rodada == 2)
    ).all()
    assert set(segunda) == {p.id for p in novos}


def test_convites_recentes_nao_expiram(db):
    """CT-RODADA-01: dentro da janela da rodada, nada expira."""
    from app.models import EstadoConvite
    from app.services import match

    _convites_antigos(db, [EstadoConvite.SUGERIDO, EstadoConvite.SUGERIDO], 1)

    assert match.expirar_convites_sem_resposta(db) == 0


def test_vaga_publicada_expira_pelo_prazo_ou_pelo_inicio(db):
    """CT-RODADA-02: a varredura expira vaga publicada cujo prazo ou início já passou."""
    from app.models import EstadoVaga
    from app.services import vagas as vagas_servico
    from app.tempo import agora
    from tests.fabricas import criar_categoria, criar_vaga

    vaga = criar_vaga(db, categoria=criar_categoria(db))
    vaga.estado = EstadoVaga.PUBLICADA
    vaga.prazo_em = agora() - timedelta(minutes=1)
    db.commit()

    assert vagas_servico.expirar_vagas_publicadas(db) == 1

    db.refresh(vaga)
    assert vaga.estado == EstadoVaga.EXPIRADA


# ---------------------------------------------------------------- 5. pino aproximado para quem não


def test_pino_exato_so_para_as_partes_da_vaga(cliente, db):
    """CT-PRIV-01: contratante e profissional veem o pino exato; outros logados, aproximado."""
    from app.models import Endereco, TipoPerfil
    from tests.fabricas import criar_usuario

    cenario = criar_cenario_vaga(db, estado=EstadoVaga.CONTRATADA)
    endereco = db.get(Endereco, cenario.vaga.endereco_id)
    endereco.latitude, endereco.longitude = -8.123456, -34.904321
    db.commit()
    estranho = criar_usuario(
        db, perfis=(TipoPerfil.PROFISSIONAL,), perfil_ativo=TipoPerfil.PROFISSIONAL
    )
    url = f"/api/v1/vagas/{cenario.vaga.id}"

    dono = cliente.get(url, headers=cabecalho(cenario.contratante)).json()
    contratado = cliente.get(url, headers=cabecalho(cenario.profissional)).json()
    outro = cliente.get(url, headers=cabecalho(estranho)).json()

    assert (dono["latitude"], dono["longitude"]) == (-8.123456, -34.904321)
    assert (contratado["latitude"], contratado["longitude"]) == (-8.123456, -34.904321)
    assert (outro["latitude"], outro["longitude"]) == (-8.12, -34.9)


# ---------------------------------------------------------------- 6. anexo do chat


def _conversa_aberta(cliente, db):
    from app.services import conversas

    cenario = criar_cenario_vaga(db, aceites_completos=True, estado=EstadoVaga.CONTRATADA)
    conversas.abrir_conversa_no_aceite(db, cenario.vaga)
    conversa = cliente.get(
        f"/api/v1/vagas/{cenario.vaga.id}/conversa", headers=cabecalho(cenario.contratante)
    )
    return cenario, conversa


@pytest.mark.parametrize(
    "anexo",
    [
        "https://wa.me/5581999999999",
        "/api/v1/conversas/9999/anexos/outro.png",
        "javascript:alert(1)",
    ],
    ids=["link-externo", "outra-conversa", "esquema-perigoso"],
)
def test_anexo_url_so_aceita_anexo_da_propria_conversa(cliente, db, anexo):
    """CT-CHAT-01: o campo anexo_url não aceita link externo nem anexo de outra conversa."""
    cenario, conversa = _conversa_aberta(cliente, db)

    resposta = cliente.post(
        f"/api/v1/conversas/{conversa.json()['id']}/mensagens",
        json={"texto": "oi", "anexo_url": anexo},
        headers=cabecalho(cenario.contratante),
    )

    assert resposta.status_code == 422
    assert resposta.json()["detail"]["codigo"] == "anexo_invalido"


def test_anexo_da_propria_conversa_e_aceito(cliente, db):
    """CT-CHAT-01: o caminho interno de anexo desta conversa continua válido."""
    cenario, conversa = _conversa_aberta(cliente, db)
    conversa_id = conversa.json()["id"]

    resposta = cliente.post(
        f"/api/v1/conversas/{conversa_id}/mensagens",
        json={"anexo_url": f"/api/v1/conversas/{conversa_id}/anexos/abc123.png"},
        headers=cabecalho(cenario.contratante),
    )

    assert resposta.status_code == 201, resposta.text


# ---------------------------------------------------------------- 7. regra de cancelamento do acor


def test_acordo_mostra_a_mesma_antecedencia_que_o_cancelamento_aplica(cliente, db, demo):
    """CT-CANC-01: o acordo usa a chave cancelamento_tardio_horas, também no perfil demo."""
    from app.services import configuracao

    cenario = criar_cenario_vaga(db, aceites_completos=True, estado=EstadoVaga.CONTRATADA)
    horas = float(configuracao.get("cancelamento_tardio_horas", db=db))

    acordo = cliente.get(
        f"/api/v1/vagas/{cenario.vaga.id}/acordo", headers=cabecalho(cenario.contratante)
    ).json()

    assert f"{horas:g} h" in acordo["regras_cancelamento"]
    assert "1 min" not in acordo["regras_cancelamento"]


# ---------------------------------------------------------------- 8. cancelamento atômico


def test_falha_no_cancelamento_desfaz_o_estorno(cliente, db, monkeypatch):
    """CT-CANC-02: se algo falha depois do estorno, nada é gravado (vaga e Pix intactos)."""
    from app.services import notificacoes, pagamentos

    fixar_relogio(BASE)
    cenario = criar_cenario_vaga(db, inicio_em=BASE + timedelta(hours=1), valor=Decimal("200.00"))
    pagamento = pagamentos.criar_pagamento(db, cenario.vaga)
    pagamentos.reter(db, pagamento)

    def falhar(*_args, **_kwargs):
        raise RuntimeError("falha simulada")

    monkeypatch.setattr(notificacoes, "criar_notificacao", falhar)

    with pytest.raises(RuntimeError):
        cliente.post(
            f"/api/v1/vagas/{cenario.vaga.id}/cancelamento",
            json={"motivo": "Imprevisto no dia."},
            headers=cabecalho(cenario.contratante),
        )

    db.expire_all()
    assert cenario.vaga.estado == EstadoVaga.CONTRATADA
    assert pagamento.estado == EstadoPagamento.RETIDO
    assert all(item.tipo.value != "estorno" for item in pagamento.lancamentos)


def test_cancelamento_com_pix_retido_estorna_em_uma_gravacao(cliente, db):
    """CT-CANC-02: o caminho feliz continua estornando e cancelando."""
    from app.services import pagamentos

    fixar_relogio(BASE)
    cenario = criar_cenario_vaga(db, inicio_em=BASE + timedelta(hours=10), valor=Decimal("200.00"))
    pagamento = pagamentos.criar_pagamento(db, cenario.vaga)
    pagamentos.reter(db, pagamento)

    resposta = cliente.post(
        f"/api/v1/vagas/{cenario.vaga.id}/cancelamento",
        json={"motivo": "Imprevisto no dia."},
        headers=cabecalho(cenario.contratante),
    )

    assert resposta.status_code == 200, resposta.text
    db.expire_all()
    assert cenario.vaga.estado == EstadoVaga.CANCELADA
    assert pagamento.estado == EstadoPagamento.ESTORNADO
