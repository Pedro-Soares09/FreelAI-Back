"""Testes do chat (US44 / FLX07): WebSocket autenticado, persistência, histórico, lista e visto."""

import time
from datetime import timedelta

import pytest
from fastapi import HTTPException
from starlette.websockets import WebSocketDisconnect

from app.models import Conversa, EstadoConta, EstadoVaga, TipoPerfil
from app.security import create_access_token, create_refresh_token
from app.services import conversas, pagamentos
from app.services.conversas import (
    WS_CODIGO_NAO_AUTENTICADO,
    WS_CODIGO_NAO_ENCONTRADA,
    WS_CODIGO_SEM_ACESSO,
)
from app.tempo import avancar_relogio
from tests.fabricas import cabecalho, criar_cenario_vaga, criar_usuario


def _token(usuario) -> str:
    return create_access_token(str(usuario.id))


# --- Regras de abertura e fechamento por estado da vaga ----------------------------------


@pytest.mark.parametrize(
    "estado_pre_aceite",
    [EstadoVaga.PUBLICADA, EstadoVaga.AGUARDANDO_ACEITE],
)
def test_nao_existe_conversa_antes_do_aceite(cliente, db, estado_pre_aceite):
    cenario = criar_cenario_vaga(db, estado=estado_pre_aceite)

    with pytest.raises(HTTPException) as erro:
        conversas.abrir_conversa_no_aceite(db, cenario.vaga)
    assert erro.value.status_code == 409
    assert erro.value.detail["codigo"] == "vaga_sem_aceite"

    consulta = cliente.get(
        f"/api/v1/vagas/{cenario.vaga.id}/conversa",
        headers=cabecalho(cenario.contratante),
    )
    assert consulta.status_code == 404
    assert consulta.json()["detail"]["codigo"] == "conversa_nao_encontrada"


def test_conversa_nasce_no_aceite_uma_por_vaga_e_integra_ids(cliente, db):
    cenario = criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)

    primeira = conversas.abrir_conversa_no_aceite(db, cenario.vaga)
    segunda = conversas.abrir_conversa_no_aceite(db, cenario.vaga)

    assert primeira.id == segunda.id
    assert primeira.vaga_id == cenario.vaga.id
    assert primeira.aberta is True

    por_vaga = cliente.get(
        f"/api/v1/vagas/{cenario.vaga.id}/conversa",
        headers=cabecalho(cenario.contratante),
    )
    assert por_vaga.status_code == 200
    assert por_vaga.json()["id"] == primeira.id
    assert por_vaga.json()["vaga_id"] == cenario.vaga.id
    assert por_vaga.json()["aberta"] is True
    assert por_vaga.json()["outra_parte"]["usuario_id"] == cenario.profissional.id

    por_conversa = cliente.get(
        f"/api/v1/conversas/{primeira.id}",
        headers=cabecalho(cenario.profissional),
    )
    assert por_conversa.status_code == 200
    assert por_conversa.json()["id"] == primeira.id
    assert por_conversa.json()["vaga_id"] == cenario.vaga.id
    assert por_conversa.json()["outra_parte"]["usuario_id"] == cenario.contratante.id


@pytest.mark.parametrize(
    "estado_queda",
    [
        EstadoVaga.CANCELADA,
        EstadoVaga.EXPIRADA,
        EstadoVaga.FALTA,
        EstadoVaga.REEMBOLSADA,
        EstadoVaga.LIBERADA,
        EstadoVaga.LIBERADA_PARCIAL,
    ],
)
def test_conversa_fecha_para_escrita_quando_a_vaga_cai_ou_encerra(
    cliente, db, estado_queda
):
    cenario = criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)
    conversa = conversas.abrir_conversa_no_aceite(db, cenario.vaga)

    # Envia uma mensagem enquanto a vaga está aceita
    primeira = cliente.post(
        f"/api/v1/conversas/{conversa.id}/mensagens",
        json={"texto": "Mensagem antes da queda"},
        headers=cabecalho(cenario.contratante),
    )
    assert primeira.status_code == 201

    # A vaga cai ou encerra
    cenario.vaga.estado = estado_queda
    db.commit()

    detalhe = cliente.get(
        f"/api/v1/conversas/{conversa.id}",
        headers=cabecalho(cenario.contratante),
    )
    assert detalhe.status_code == 200
    assert detalhe.json()["aberta"] is False

    # Escrita via REST é recusada com 409 conversa_fechada
    bloqueada = cliente.post(
        f"/api/v1/conversas/{conversa.id}/mensagens",
        json={"texto": "Tentativa após fechamento"},
        headers=cabecalho(cenario.profissional),
    )
    assert bloqueada.status_code == 409
    assert bloqueada.json()["detail"]["codigo"] == "conversa_fechada"

    # Leitura do histórico continua disponível
    historico = cliente.get(
        f"/api/v1/conversas/{conversa.id}/mensagens",
        headers=cabecalho(cenario.profissional),
    )
    assert historico.status_code == 200
    assert len(historico.json()) == 1


def test_expiracao_do_pagamento_fecha_a_conversa_da_vaga(db):
    cenario = criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)
    conversa = conversas.abrir_conversa_no_aceite(db, cenario.vaga)
    assert conversa.aberta is True

    pagamentos.criar_pagamento(db, cenario.vaga)
    avancar_relogio(timedelta(hours=2))
    pagamentos.expirar_pagamentos(db)

    db.refresh(conversa)
    assert conversa.aberta is False


# --- WebSocket: autenticação na query e autorização por vaga -----------------------------


def test_websocket_recusa_sem_token_ou_com_token_invalido_com_4401(cliente, db):
    cenario = criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)
    conversa = conversas.abrir_conversa_no_aceite(db, cenario.vaga)
    refresh = create_refresh_token(str(cenario.contratante.id))
    banido = criar_usuario(db, estado=EstadoConta.BANIDA)

    for sufixo in (
        "",
        "?token=token-invalido",
        f"?token={refresh}",
        f"?token={_token(banido)}",
    ):
        with pytest.raises(WebSocketDisconnect) as erro:
            with cliente.websocket_connect(f"/api/v1/conversas/{conversa.id}/ws{sufixo}"):
                pass
        assert erro.value.code == WS_CODIGO_NAO_AUTENTICADO


def test_websocket_recusa_usuario_de_outra_vaga_com_4403(cliente, db):
    cenario = criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)
    conversa = conversas.abrir_conversa_no_aceite(db, cenario.vaga)
    outro_cenario = criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)

    for intruso in (outro_cenario.contratante, outro_cenario.profissional):
        with pytest.raises(WebSocketDisconnect) as erro:
            with cliente.websocket_connect(
                f"/api/v1/conversas/{conversa.id}/ws?token={_token(intruso)}"
            ):
                pass
        assert erro.value.code == WS_CODIGO_SEM_ACESSO


def test_websocket_recusa_conversa_inexistente_com_4404(cliente, db):
    usuario = criar_usuario(db, perfis=(TipoPerfil.CONTRATANTE,))

    with pytest.raises(WebSocketDisconnect) as erro:
        with cliente.websocket_connect(f"/api/v1/conversas/9999/ws?token={_token(usuario)}"):
            pass
    assert erro.value.code == WS_CODIGO_NAO_ENCONTRADA


# --- WebSocket: tempo real (< 1s) e persistência no histórico ----------------------------


def test_mensagem_via_websocket_chega_ao_outro_lado_em_menos_de_1s_e_fica_no_historico(
    cliente, db
):
    cenario = criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)
    conversa = conversas.abrir_conversa_no_aceite(db, cenario.vaga)

    url_cont = f"/api/v1/conversas/{conversa.id}/ws?token={_token(cenario.contratante)}"
    url_prof = f"/api/v1/conversas/{conversa.id}/ws?token={_token(cenario.profissional)}"

    with (
        cliente.websocket_connect(url_cont) as ws_contratante,
        cliente.websocket_connect(url_prof) as ws_profissional,
    ):
        inicio = time.perf_counter()
        ws_contratante.send_json({"texto": "Olá, tudo certo para amanhã?"})
        eco_remetente = ws_contratante.receive_json()
        recebida_destinatario = ws_profissional.receive_json()
        decorrido = time.perf_counter() - inicio

        assert decorrido < 1.0
        assert recebida_destinatario == eco_remetente
        assert recebida_destinatario["conversa_id"] == conversa.id
        assert recebida_destinatario["autor_id"] == cenario.contratante.id
        assert recebida_destinatario["texto"] == "Olá, tudo certo para amanhã?"
        assert recebida_destinatario["lida_em"] is None

        ws_profissional.send_json(
            {
                "texto": "Tudo certo! Segue a foto das ferramentas.",
                "anexo_url": "/api/v1/arquivos/anexo/ferramentas.jpg",
            }
        )
        resposta_cont = ws_contratante.receive_json()
        resposta_prof = ws_profissional.receive_json()
        assert resposta_cont == resposta_prof
        assert resposta_cont["autor_id"] == cenario.profissional.id
        assert resposta_cont["anexo_url"] == "/api/v1/arquivos/anexo/ferramentas.jpg"

    historico = cliente.get(
        f"/api/v1/conversas/{conversa.id}/mensagens",
        headers=cabecalho(cenario.contratante),
    )
    assert historico.status_code == 200
    itens = historico.json()
    assert len(itens) == 2
    assert [m["texto"] for m in itens] == [
        "Olá, tudo certo para amanhã?",
        "Tudo certo! Segue a foto das ferramentas.",
    ]


def test_websocket_recusa_escrita_quando_conversa_fecha_durante_conexao(cliente, db):
    cenario = criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)
    conversa = conversas.abrir_conversa_no_aceite(db, cenario.vaga)
    url = f"/api/v1/conversas/{conversa.id}/ws?token={_token(cenario.contratante)}"

    with cliente.websocket_connect(url) as ws:
        # Vaga cai enquanto o socket está aberto
        cenario.vaga.estado = EstadoVaga.CANCELADA
        db.commit()

        ws.send_json({"texto": "Ainda está aí?"})
        resposta = ws.receive_json()

        assert resposta["tipo"] == "erro"
        assert resposta["codigo"] == "conversa_fechada"

    historico = cliente.get(
        f"/api/v1/conversas/{conversa.id}/mensagens",
        headers=cabecalho(cenario.contratante),
    )
    assert historico.json() == []


# --- REST: lista de conversas, histórico, não lidas e visto ------------------------------


def test_lista_conversas_com_ultima_mensagem_nao_lidas_e_marcacao_de_visto(cliente, db):
    cenario = criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)
    conversa = conversas.abrir_conversa_no_aceite(db, cenario.vaga)

    # Profissional envia duas mensagens
    for texto in ("Bom dia!", "Chego às 14h."):
        envio = cliente.post(
            f"/api/v1/conversas/{conversa.id}/mensagens",
            json={"texto": texto},
            headers=cabecalho(cenario.profissional),
        )
        assert envio.status_code == 201

    # Para o contratante: 2 não lidas e última mensagem preenchida
    lista_cont = cliente.get("/api/v1/conversas", headers=cabecalho(cenario.contratante))
    assert lista_cont.status_code == 200
    resumo_cont = lista_cont.json()[0]
    assert resumo_cont["id"] == conversa.id
    assert resumo_cont["vaga_id"] == cenario.vaga.id
    assert resumo_cont["nao_lidas"] == 2
    assert resumo_cont["ultima_mensagem"]["texto"] == "Chego às 14h."
    assert resumo_cont["ultima_mensagem"]["autor_id"] == cenario.profissional.id
    assert resumo_cont["outra_parte"]["usuario_id"] == cenario.profissional.id

    # Para o próprio autor (profissional): 0 não lidas
    lista_prof = cliente.get("/api/v1/conversas", headers=cabecalho(cenario.profissional))
    assert lista_prof.status_code == 200
    assert lista_prof.json()[0]["nao_lidas"] == 0

    # Contratante marca como visto
    visto = cliente.post(
        f"/api/v1/conversas/{conversa.id}/visto",
        headers=cabecalho(cenario.contratante),
    )
    assert visto.status_code == 200
    assert visto.json()["marcadas"] == 2
    assert visto.json()["lida_em"] is not None

    # Segunda chamada é idempotente (0 marcadas)
    visto_repetido = cliente.post(
        f"/api/v1/conversas/{conversa.id}/visto",
        headers=cabecalho(cenario.contratante),
    )
    assert visto_repetido.status_code == 200
    assert visto_repetido.json()["marcadas"] == 0

    # Contador de não lidas zera e histórico traz `lida_em` preenchido
    resumo_atualizado = cliente.get(
        f"/api/v1/conversas/{conversa.id}",
        headers=cabecalho(cenario.contratante),
    ).json()
    assert resumo_atualizado["nao_lidas"] == 0

    historico = cliente.get(
        f"/api/v1/conversas/{conversa.id}/mensagens",
        headers=cabecalho(cenario.profissional),
    ).json()
    assert all(m["lida_em"] is not None for m in historico)


def test_rotas_rest_recusam_terceiro_e_nao_autenticado(cliente, db):
    cenario = criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)
    conversa: Conversa = conversas.abrir_conversa_no_aceite(db, cenario.vaga)
    terceiro = criar_usuario(
        db,
        perfis=(TipoPerfil.CONTRATANTE, TipoPerfil.PROFISSIONAL),
        perfil_ativo=TipoPerfil.CONTRATANTE,
    )

    assert cliente.get("/api/v1/conversas").status_code == 401
    assert cliente.get(f"/api/v1/conversas/{conversa.id}").status_code == 401
    assert cliente.get(f"/api/v1/conversas/{conversa.id}/mensagens").status_code == 401
    assert cliente.post(f"/api/v1/conversas/{conversa.id}/visto").status_code == 401

    for rota in (
        f"/api/v1/conversas/{conversa.id}",
        f"/api/v1/vagas/{cenario.vaga.id}/conversa",
        f"/api/v1/conversas/{conversa.id}/mensagens",
    ):
        negado = cliente.get(rota, headers=cabecalho(terceiro))
        assert negado.status_code == 403
        assert negado.json()["detail"]["codigo"] == "sem_acesso_a_conversa"

    envio_negado = cliente.post(
        f"/api/v1/conversas/{conversa.id}/mensagens",
        json={"texto": "Intruso"},
        headers=cabecalho(terceiro),
    )
    assert envio_negado.status_code == 403

    visto_negado = cliente.post(
        f"/api/v1/conversas/{conversa.id}/visto",
        headers=cabecalho(terceiro),
    )
    assert visto_negado.status_code == 403

    # Terceiro só vê suas próprias conversas (lista vazia)
    assert cliente.get("/api/v1/conversas", headers=cabecalho(terceiro)).json() == []
