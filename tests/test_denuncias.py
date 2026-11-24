"""Testes da US57 (SEG05): denúncia (chat e perfil), fila do admin com tempo e decisão."""

from datetime import UTC, datetime, timedelta

import pytest

from app.models import (
    Conversa,
    Denuncia,
    EstadoConta,
    EstadoDenuncia,
    EstadoVaga,
    OrigemDenuncia,
    PapelUsuario,
    TipoPerfil,
)
from app.services import denuncias
from app.tempo import avancar_relogio, fixar_relogio
from tests.fabricas import cabecalho, criar_cenario_vaga, criar_usuario


def _proxima_quinta_14h() -> datetime:
    hoje = datetime.now(UTC).replace(hour=14, minute=0, second=0, microsecond=0)
    dias_ate_quinta = (3 - hoje.weekday()) % 7
    candidata = hoje + timedelta(days=dias_ate_quinta)
    if candidata <= datetime.now(UTC):
        candidata += timedelta(days=7)
    return candidata


QUINTA = _proxima_quinta_14h()


# --- Abertura de denúncia (perfil, vaga e conversa do chat) ------------------------------


def test_denuncia_por_perfil_vaga_e_chat_entra_na_fila_com_tempo_em_espera(cliente, db):
    fixar_relogio(QUINTA)
    cenario = criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)
    conversa = Conversa(vaga_id=cenario.vaga.id, aberta=True)
    db.add(conversa)
    db.commit()
    db.refresh(conversa)
    admin = criar_usuario(db, papel=PapelUsuario.ADMIN)

    # 1. Denúncia pelo chat (inferindo denunciado_id e vaga_id a partir de conversa_id)
    via_chat = cliente.post(
        "/api/v1/denuncias",
        json={
            "conversa_id": conversa.id,
            "motivo": "negociacao_fora",
            "descricao": "Pediu pagamento por fora no chat.",
        },
        headers=cabecalho(cenario.contratante),
    )
    assert via_chat.status_code == 201
    corpo_chat = via_chat.json()
    assert corpo_chat["denunciado_id"] == cenario.profissional.id
    assert corpo_chat["vaga_id"] == cenario.vaga.id
    assert corpo_chat["conversa_id"] == conversa.id
    assert corpo_chat["estado"] == "aberta"
    assert "denunciante_id" not in corpo_chat

    # 2. Denúncia por perfil (apenas denunciado_id)
    avancar_relogio(timedelta(hours=2))
    via_perfil = cliente.post(
        "/api/v1/denuncias",
        json={
            "denunciado_id": cenario.contratante.id,
            "motivo": "perfil_falso",
            "descricao": "Dados inconsistentes no perfil.",
        },
        headers=cabecalho(cenario.profissional),
    )
    assert via_perfil.status_code == 201
    assert via_perfil.json()["denunciado_id"] == cenario.contratante.id
    assert via_perfil.json()["vaga_id"] is None
    assert via_perfil.json()["conversa_id"] is None

    # 3. Denúncia automática (US46) na mesma fila
    avancar_relogio(timedelta(hours=1))
    db.add(
        Denuncia(
            denunciante_id=None,
            denunciado_id=cenario.profissional.id,
            vaga_id=cenario.vaga.id,
            conversa_id=conversa.id,
            origem=OrigemDenuncia.AUTOMATICA,
            motivo="contato_externo_no_chat",
            descricao="Tentativa de enviar telefone 3 vezes.",
            estado=EstadoDenuncia.ABERTA,
        )
    )
    db.commit()

    # Avança mais 1h (total desde QUINTA: 4h para a 1ª, 2h para a 2ª, 1h para a 3ª)
    avancar_relogio(timedelta(hours=1))

    fila = cliente.get("/api/v1/admin/denuncias", headers=cabecalho(admin))
    assert fila.status_code == 200
    itens = fila.json()
    assert len(itens) == 3

    # Ordem FIFO: a mais antiga (maior tempo em espera) vem primeiro
    assert [i["motivo"] for i in itens] == [
        "negociacao_fora",
        "perfil_falso",
        "contato_externo_no_chat",
    ]
    assert [i["origem"] for i in itens] == ["usuario", "usuario", "automatica"]
    assert [i["tempo_espera_minutos"] for i in itens] == [240, 120, 60]
    assert [i["tempo_espera_horas_uteis"] for i in itens] == [4.0, 2.0, 1.0]
    assert all(i["meta_horas_uteis"] == 48 for i in itens)
    assert all(i["acima_da_meta"] is False for i in itens)


def test_calculo_de_horas_uteis_desconta_fim_de_semana_e_sinaliza_meta_48h(cliente, db):
    # Quinta 14:00 UTC -> +10h na quinta + 24h na sexta = 34h úteis até sábado 00:00
    # Sábado e domingo (48h corridas) = 0h úteis
    # Segunda inteira (24h) = 58h úteis acumuladas (> 48h úteis)
    fixar_relogio(QUINTA)
    denunciante = criar_usuario(db)
    denunciado = criar_usuario(db)
    admin = criar_usuario(db, papel=PapelUsuario.ADMIN)

    cliente.post(
        "/api/v1/denuncias",
        json={"denunciado_id": denunciado.id, "motivo": "conduta_grave"},
        headers=cabecalho(denunciante),
    )

    # No domingo às 12:00 UTC: passaram 70h corridas, mas apenas 34h úteis (abaixo de 48h úteis)
    fixar_relogio(QUINTA + timedelta(days=2, hours=22))
    item_domingo = cliente.get("/api/v1/admin/denuncias", headers=cabecalho(admin)).json()[0]
    assert item_domingo["tempo_espera_horas_uteis"] == pytest.approx(34.0)
    assert item_domingo["acima_da_meta"] is False

    # Na segunda às 18:00 UTC: 34h + 18h = 52h úteis (acima da meta de 48h úteis)
    fixar_relogio(QUINTA + timedelta(days=4, hours=4))
    item_segunda = cliente.get("/api/v1/admin/denuncias", headers=cabecalho(admin)).json()[0]
    assert item_segunda["tempo_espera_horas_uteis"] == pytest.approx(52.0)
    assert item_segunda["acima_da_meta"] is True


# --- Privacidade: denunciado nunca vê quem denunciou -------------------------------------


def test_denunciado_nao_ve_quem_denunciou(cliente, db):
    denunciante = criar_usuario(db)
    denunciado = criar_usuario(db)
    terceiro = criar_usuario(db)

    criada = cliente.post(
        "/api/v1/denuncias",
        json={
            "denunciado_id": denunciado.id,
            "motivo": "desrespeito",
            "descricao": "Mensagem ofensiva.",
        },
        headers=cabecalho(denunciante),
    )
    assert criada.status_code == 201
    denuncia_id = criada.json()["id"]

    # Consulta da lista pelo denunciado: não contém `denunciante_id`
    lista_denunciado = cliente.get("/api/v1/denuncias", headers=cabecalho(denunciado))
    assert lista_denunciado.status_code == 200
    assert len(lista_denunciado.json()) == 1
    assert "denunciante_id" not in lista_denunciado.json()[0]
    assert lista_denunciado.json()[0]["denunciado_id"] == denunciado.id

    # Consulta do detalhe pelo denunciado: também não contém `denunciante_id`
    detalhe_denunciado = cliente.get(
        f"/api/v1/denuncias/{denuncia_id}",
        headers=cabecalho(denunciado),
    )
    assert detalhe_denunciado.status_code == 200
    assert "denunciante_id" not in detalhe_denunciado.json()

    # Terceiro estranho não pode consultar a denúncia
    assert (
        cliente.get(
            f"/api/v1/denuncias/{denuncia_id}",
            headers=cabecalho(terceiro),
        ).status_code
        == 403
    )


def test_recusa_autodenuncia_e_terceiro_fora_da_vaga(cliente, db):
    cenario = criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)
    terceiro = criar_usuario(db, perfis=(TipoPerfil.CONTRATANTE,))

    auto = cliente.post(
        "/api/v1/denuncias",
        json={"denunciado_id": cenario.contratante.id, "motivo": "teste"},
        headers=cabecalho(cenario.contratante),
    )
    assert auto.status_code == 409
    assert auto.json()["detail"]["codigo"] == "autodenuncia"

    intruso = cliente.post(
        "/api/v1/denuncias",
        json={"vaga_id": cenario.vaga.id, "motivo": "teste"},
        headers=cabecalho(terceiro),
    )
    assert intruso.status_code == 403
    assert intruso.json()["detail"]["codigo"] == "nao_participa_da_vaga"


# --- Decisão no admin: procedente / improcedente, impacto em reputação e US58 ------------


def test_decisao_procedente_e_improcedente_com_motivo_conta_para_us58_e_reputacao(cliente, db):
    fixar_relogio(QUINTA)
    denunciante = criar_usuario(db)
    denunciado = criar_usuario(db)
    admin = criar_usuario(db, papel=PapelUsuario.ADMIN)

    # Abre 4 denúncias contra o mesmo usuário
    ids = []
    for indice in range(4):
        resp = cliente.post(
            "/api/v1/denuncias",
            json={
                "denunciado_id": denunciado.id,
                "motivo": f"ocorrencia_{indice + 1}",
                "descricao": "Relato da ocorrência.",
            },
            headers=cabecalho(denunciante),
        )
        assert resp.status_code == 201
        ids.append(resp.json()["id"])

    # 1. Decide a primeira como improcedente -> não conta como procedente
    dec1 = cliente.post(
        f"/api/v1/admin/denuncias/{ids[0]}/decisao",
        json={"decisao": "improcedente", "motivo": "Sem evidências no histórico."},
        headers=cabecalho(admin),
    )
    assert dec1.status_code == 200
    assert dec1.json()["estado"] == "improcedente"
    assert dec1.json()["decisao_motivo"] == "Sem evidências no histórico."
    assert dec1.json()["decidido_em"] is not None
    assert dec1.json()["denuncias_procedentes_acumuladas"] == 0
    assert denuncias.total_procedentes_do_usuario(db, denunciado.id) == 0

    # Repetir decisão na mesma denúncia retorna 409
    repetida = cliente.post(
        f"/api/v1/admin/denuncias/{ids[0]}/decisao",
        json={"decisao": "procedente", "motivo": "Tentativa duplicada."},
        headers=cabecalho(admin),
    )
    assert repetida.status_code == 409
    assert repetida.json()["detail"]["codigo"] == "denuncia_ja_decidida"

    # 2. Decide 2 denúncias como procedentes -> acumuladas = 2 (conta ainda ativa)
    for denuncia_id in (ids[1], ids[2]):
        dec = cliente.post(
            f"/api/v1/admin/denuncias/{denuncia_id}/decisao",
            json={"decisao": "procedente", "motivo": "Comprovado pelo histórico."},
            headers=cabecalho(admin),
        )
        assert dec.status_code == 200
        assert dec.json()["estado"] == "procedente"

    db.refresh(denunciado)
    assert denuncias.total_procedentes_do_usuario(db, denunciado.id) == 2
    assert denunciado.estado == EstadoConta.ATIVA

    # 3. Decide a 3ª denúncia procedente -> atinge `denuncias_para_suspensao` (3) e suspende (US58)
    dec3 = cliente.post(
        f"/api/v1/admin/denuncias/{ids[3]}/decisao",
        json={"decisao": "procedente", "motivo": "Terceira infração confirmada."},
        headers=cabecalho(admin),
    )
    assert dec3.status_code == 200
    assert dec3.json()["denuncias_procedentes_acumuladas"] == 3

    db.refresh(denunciado)
    assert denunciado.estado == EstadoConta.SUSPENSA
    assert denunciado.suspensa_ate is not None
