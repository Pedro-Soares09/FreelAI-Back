"""Testes do chat (US44 / FLX07): WebSocket autenticado, persistência, histórico, lista e visto."""

import time
from datetime import timedelta
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker
from starlette.websockets import WebSocketDisconnect

from app.models import Conversa, EstadoConta, EstadoVaga, PapelUsuario, TipoPerfil
from app.security import create_access_token, create_refresh_token
from app.services import conversas, pagamentos
from app.services.conversas import (
    MOTIVO_DENUNCIA_CONTATO,
    WS_CODIGO_NAO_AUTENTICADO,
    WS_CODIGO_NAO_ENCONTRADA,
    WS_CODIGO_SEM_ACESSO,
)
from app.tempo import avancar_relogio
from tests.fabricas import cabecalho, criar_cenario_vaga, criar_usuario


def _token(usuario) -> str:
    return create_access_token(str(usuario.id))


@pytest.fixture(autouse=True)
def _pasta_anexos_temporaria(monkeypatch, tmp_path):
    pasta = tmp_path / "anexos"

    def _obter() -> Path:
        pasta.mkdir(parents=True, exist_ok=True)
        return pasta

    monkeypatch.setattr(conversas, "obter_pasta_anexos", _obter)
    return pasta


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
def test_conversa_fecha_para_escrita_quando_a_vaga_cai_ou_encerra(cliente, db, estado_queda):
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


def test_mensagem_via_websocket_chega_ao_outro_lado_em_menos_de_1s_e_fica_no_historico(cliente, db):
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


# --- Sessão curta por mensagem, primeira mensagem e cabeçalho Bearer --------------------


def test_websocket_nao_mantem_sessao_de_banco_aberta_enquanto_conectado(cliente, db):
    from app.db import get_db
    from app.main import app

    cenario = criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)
    conversa = conversas.abrir_conversa_no_aceite(db, cenario.vaga)
    provedor_original = app.dependency_overrides[get_db]
    contadores = {"abertas": 0, "total_aberturas": 0}

    def get_db_rastreado():
        gen = provedor_original()
        sessao = next(gen)
        contadores["abertas"] += 1
        contadores["total_aberturas"] += 1
        try:
            yield sessao
        finally:
            contadores["abertas"] -= 1
            try:
                next(gen)
            except StopIteration:
                pass

    app.dependency_overrides[get_db] = get_db_rastreado
    try:
        url = f"/api/v1/conversas/{conversa.id}/ws?token={_token(cenario.contratante)}"
        with cliente.websocket_connect(url) as ws:
            assert contadores["abertas"] == 0
            assert contadores["total_aberturas"] == 1

            ws.send_json({"texto": "Sessão curta por mensagem"})
            recebida = ws.receive_json()

            assert recebida["texto"] == "Sessão curta por mensagem"
            assert contadores["abertas"] == 0
            assert contadores["total_aberturas"] == 2
    finally:
        app.dependency_overrides[get_db] = provedor_original


def test_websocket_aceita_autenticacao_na_primeira_mensagem_sem_token_na_url(cliente, db):
    cenario = criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)
    conversa = conversas.abrir_conversa_no_aceite(db, cenario.vaga)
    url = f"/api/v1/conversas/{conversa.id}/ws?auth=mensagem"

    with (
        cliente.websocket_connect(url) as ws_contratante,
        cliente.websocket_connect(url) as ws_profissional,
    ):
        ws_contratante.send_json({"tipo": "auth", "token": _token(cenario.contratante)})
        ack_cont = ws_contratante.receive_json()
        assert ack_cont == {
            "tipo": "autenticado",
            "conversa_id": conversa.id,
            "usuario_id": cenario.contratante.id,
        }

        ws_profissional.send_json({"tipo": "auth", "token": _token(cenario.profissional)})
        ack_prof = ws_profissional.receive_json()
        assert ack_prof == {
            "tipo": "autenticado",
            "conversa_id": conversa.id,
            "usuario_id": cenario.profissional.id,
        }

        ws_contratante.send_json({"texto": "Token nunca foi na URL!"})
        eco = ws_contratante.receive_json()
        recebida = ws_profissional.receive_json()
        assert eco == recebida
        assert recebida["texto"] == "Token nunca foi na URL!"

    historico = cliente.get(
        f"/api/v1/conversas/{conversa.id}/mensagens",
        headers=cabecalho(cenario.contratante),
    )
    assert [m["texto"] for m in historico.json()] == ["Token nunca foi na URL!"]


def test_websocket_auth_na_primeira_mensagem_recusa_invalido_timeout_e_intruso(
    cliente, db, monkeypatch
):
    from app.api.v1 import conversas as rotas_conversas

    cenario = criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)
    conversa = conversas.abrir_conversa_no_aceite(db, cenario.vaga)
    outro = criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)
    url = f"/api/v1/conversas/{conversa.id}/ws?auth=mensagem"

    for carga in (
        {"tipo": "auth", "token": "invalido"},
        {"tipo": "auth", "token": ""},
        {"tipo": "outro", "token": _token(cenario.contratante)},
        {"texto": "sem autenticar"},
    ):
        with cliente.websocket_connect(url) as ws:
            ws.send_json(carga)
            with pytest.raises(WebSocketDisconnect) as erro:
                ws.receive_json()
            assert erro.value.code == WS_CODIGO_NAO_AUTENTICADO

    # Intruso de outra vaga -> 4403
    with cliente.websocket_connect(url) as ws:
        ws.send_json({"tipo": "auth", "token": _token(outro.contratante)})
        with pytest.raises(WebSocketDisconnect) as erro:
            ws.receive_json()
        assert erro.value.code == WS_CODIGO_SEM_ACESSO

    # Conversa inexistente -> 4404
    with cliente.websocket_connect("/api/v1/conversas/9999/ws?auth=mensagem") as ws:
        ws.send_json({"tipo": "auth", "token": _token(cenario.contratante)})
        with pytest.raises(WebSocketDisconnect) as erro:
            ws.receive_json()
        assert erro.value.code == WS_CODIGO_NAO_ENCONTRADA

    # Timeout esperando a primeira mensagem -> 4401
    monkeypatch.setattr(rotas_conversas, "WS_TIMEOUT_AUTH_SEGUNDOS", 0.05)
    with cliente.websocket_connect(url) as ws:
        with pytest.raises(WebSocketDisconnect) as erro:
            ws.receive_json()
        assert erro.value.code == WS_CODIGO_NAO_AUTENTICADO


def test_websocket_aceita_cabecalho_authorization_bearer(cliente, db):
    cenario = criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)
    conversa = conversas.abrir_conversa_no_aceite(db, cenario.vaga)

    with cliente.websocket_connect(
        f"/api/v1/conversas/{conversa.id}/ws",
        headers=cabecalho(cenario.contratante),
    ) as ws:
        ws.send_json({"texto": "Via header Authorization"})
        assert ws.receive_json()["texto"] == "Via header Authorization"

    historico = cliente.get(
        f"/api/v1/conversas/{conversa.id}/mensagens",
        headers=cabecalho(cenario.contratante),
    )
    assert [m["texto"] for m in historico.json()] == ["Via header Authorization"]


# --- Bloqueio de contato externo, contador persistido, denúncia e anexos (US46) --------


@pytest.mark.parametrize(
    "mensagem_bloqueada",
    [
        "Meu número é 81.9.9999.9999",
        "Liga para 81 9 9999 9999",
        "Novo numero 81.99999.9999",
        "Contato 81 99999 9999",
        "Chama no +55.81.9.8888.7777",
        "Me liga +55 81 9 8888 7777",
        "Fixo 81.3333.4444",
        "Fixo 81 3333 4444",
        "Fala no 9.9999.9999",
        "Ou no 9999 8888",
        "Salva aí (81) 99999-9999",
        "Sem máscara 81999998888",
        "Passa seu WhatsApp",
        "Chama no zap mais tarde",
        "Manda no wpp",
        "Link wa.me/5581999999999",
    ],
)
def test_filtro_barra_telefone_brasileiro_e_palavras_de_whatsapp(cliente, db, mensagem_bloqueada):
    cenario = criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)
    conversa = conversas.abrir_conversa_no_aceite(db, cenario.vaga)

    resposta = cliente.post(
        f"/api/v1/conversas/{conversa.id}/mensagens",
        json={"texto": mensagem_bloqueada},
        headers=cabecalho(cenario.contratante),
    )
    assert resposta.status_code == 422
    detalhe = resposta.json()["detail"]
    assert detalhe["codigo"] == "contato_externo_bloqueado"
    assert detalhe["tentativas"] == 1
    assert detalhe["denuncia_aberta"] is False

    historico = cliente.get(
        f"/api/v1/conversas/{conversa.id}/mensagens",
        headers=cabecalho(cenario.contratante),
    )
    assert historico.json() == []


def test_filtro_permite_mensagens_normais_com_horarios_e_valores(cliente, db):
    cenario = criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)
    conversa = conversas.abrir_conversa_no_aceite(db, cenario.vaga)
    mensagens_normais = [
        "Dia 20.11.2026 às 14h no local combinado",
        "Valor acordado R$ 1.500,00 via plataforma",
        "CEP 50000-000, bloco 12, sala 304",
        "Pedido 2026/11 confirmado para amanhã",
    ]

    for texto in mensagens_normais:
        resposta = cliente.post(
            f"/api/v1/conversas/{conversa.id}/mensagens",
            json={"texto": texto},
            headers=cabecalho(cenario.contratante),
        )
        assert resposta.status_code == 201, texto

    historico = cliente.get(
        f"/api/v1/conversas/{conversa.id}/mensagens",
        headers=cabecalho(cenario.profissional),
    )
    assert [m["texto"] for m in historico.json()] == mensagens_normais


def test_websocket_barra_telefone_envia_aviso_ao_remetente_e_nao_transmite_ao_outro_lado(
    cliente, db
):
    cenario = criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)
    conversa = conversas.abrir_conversa_no_aceite(db, cenario.vaga)

    with (
        cliente.websocket_connect(
            f"/api/v1/conversas/{conversa.id}/ws?token={_token(cenario.contratante)}"
        ) as ws_contratante,
        cliente.websocket_connect(
            f"/api/v1/conversas/{conversa.id}/ws?token={_token(cenario.profissional)}"
        ) as ws_profissional,
    ):
        ws_contratante.send_json({"texto": "Me chama no 81 9 9999 9999"})
        aviso = ws_contratante.receive_json()
        assert aviso["tipo"] == "erro"
        assert aviso["codigo"] == "contato_externo_bloqueado"
        assert aviso["tentativas"] == 1
        assert aviso["denuncia_aberta"] is False

        ws_contratante.send_json({"texto": "Combinado às 14h"})
        confirmada = ws_contratante.receive_json()
        recebida = ws_profissional.receive_json()
        assert confirmada["texto"] == "Combinado às 14h"
        assert recebida["texto"] == "Combinado às 14h"


def test_terceira_tentativa_cria_denuncia_automatica_visivel_no_admin(cliente, db):
    cenario = criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)
    conversa = conversas.abrir_conversa_no_aceite(db, cenario.vaga)
    admin = criar_usuario(db, papel=PapelUsuario.ADMIN)

    # Usuário comum não acessa painel admin de denúncias
    negado = cliente.get("/api/v1/admin/denuncias", headers=cabecalho(cenario.contratante))
    assert negado.status_code == 403

    for tentativa in (1, 2):
        resp = cliente.post(
            f"/api/v1/conversas/{conversa.id}/mensagens",
            json={"texto": f"Tentativa {tentativa}: 81.9.9999.9999"},
            headers=cabecalho(cenario.contratante),
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["tentativas"] == tentativa
        assert resp.json()["detail"]["denuncia_aberta"] is False

    assert cliente.get("/api/v1/admin/denuncias", headers=cabecalho(admin)).json() == []

    terceira = cliente.post(
        f"/api/v1/conversas/{conversa.id}/mensagens",
        json={"texto": "Tentativa 3: chama no whatsapp"},
        headers=cabecalho(cenario.contratante),
    )
    assert terceira.status_code == 422
    assert terceira.json()["detail"]["tentativas"] == 3
    assert terceira.json()["detail"]["denuncia_aberta"] is True

    denuncias = cliente.get(
        "/api/v1/admin/denuncias?origem=automatica&estado=aberta",
        headers=cabecalho(admin),
    )
    assert denuncias.status_code == 200
    itens = denuncias.json()
    assert len(itens) == 1
    assert itens[0]["denunciante_id"] is None
    assert itens[0]["denunciado_id"] == cenario.contratante.id
    assert itens[0]["vaga_id"] == cenario.vaga.id
    assert itens[0]["conversa_id"] == conversa.id
    assert itens[0]["origem"] == "automatica"
    assert itens[0]["motivo"] == MOTIVO_DENUNCIA_CONTATO
    assert itens[0]["estado"] == "aberta"


def test_contador_de_tentativas_persiste_no_banco_entre_sessoes_independentes(cliente, db, engine):
    cenario = criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)
    conversa = conversas.abrir_conversa_no_aceite(db, cenario.vaga)

    for tentativa in (1, 2):
        resp = cliente.post(
            f"/api/v1/conversas/{conversa.id}/mensagens",
            json={"texto": f"Tenta zap {tentativa}"},
            headers=cabecalho(cenario.profissional),
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["tentativas"] == tentativa

    sessao_fabrica = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    with sessao_fabrica() as sessao_nova:
        assert (
            conversas.tentativas_de_contato(sessao_nova, conversa.id, cenario.profissional.id) == 2
        )

    terceira = cliente.post(
        f"/api/v1/conversas/{conversa.id}/mensagens",
        json={"texto": "Tenta 81 9 9999 9999"},
        headers=cabecalho(cenario.profissional),
    )
    assert terceira.status_code == 422
    assert terceira.json()["detail"]["tentativas"] == 3
    assert terceira.json()["detail"]["denuncia_aberta"] is True

    with sessao_fabrica() as sessao_final:
        assert (
            conversas.tentativas_de_contato(sessao_final, conversa.id, cenario.profissional.id) == 3
        )


def test_envio_de_anexo_de_imagem_e_pdf_no_chat_e_recusa_acima_de_10_mb(
    cliente, db, _pasta_anexos_temporaria
):
    cenario = criar_cenario_vaga(db, estado=EstadoVaga.ACEITA)
    conversa = conversas.abrir_conversa_no_aceite(db, cenario.vaga)
    terceiro = criar_usuario(
        db,
        perfis=(TipoPerfil.CONTRATANTE,),
        perfil_ativo=TipoPerfil.CONTRATANTE,
    )

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"conteudo-png-valido"
    pdf_bytes = b"%PDF-1.4\n1 0 obj\n%%EOF"

    with cliente.websocket_connect(
        f"/api/v1/conversas/{conversa.id}/ws?token={_token(cenario.profissional)}"
    ) as ws_profissional:
        envio_png = cliente.post(
            f"/api/v1/conversas/{conversa.id}/anexos",
            files={"arquivo": ("local.png", BytesIO(png_bytes), "image/png")},
            data={"texto": "Foto do local"},
            headers=cabecalho(cenario.contratante),
        )
        assert envio_png.status_code == 201
        corpo_png = envio_png.json()
        assert corpo_png["texto"] == "Foto do local"
        assert corpo_png["anexo_url"].startswith(f"/api/v1/conversas/{conversa.id}/anexos/")
        assert corpo_png["anexo_url"].endswith(".png")

        recebida_ws = ws_profissional.receive_json()
        assert recebida_ws["anexo_url"] == corpo_png["anexo_url"]
        assert recebida_ws["texto"] == "Foto do local"

    # Participante baixa o anexo; terceiro e não autenticado são bloqueados
    assert cliente.get(corpo_png["anexo_url"]).status_code == 401
    assert cliente.get(corpo_png["anexo_url"], headers=cabecalho(terceiro)).status_code == 403
    download_png = cliente.get(corpo_png["anexo_url"], headers=cabecalho(cenario.profissional))
    assert download_png.status_code == 200
    assert download_png.headers["content-type"].startswith("image/png")
    assert download_png.content == png_bytes

    # PDF sem legenda
    envio_pdf = cliente.post(
        f"/api/v1/conversas/{conversa.id}/anexos",
        files={"arquivo": ("briefing.pdf", BytesIO(pdf_bytes), "application/pdf")},
        headers=cabecalho(cenario.profissional),
    )
    assert envio_pdf.status_code == 201
    corpo_pdf = envio_pdf.json()
    assert corpo_pdf["texto"] is None
    assert corpo_pdf["anexo_url"].endswith(".pdf")
    download_pdf = cliente.get(corpo_pdf["anexo_url"], headers=cabecalho(cenario.contratante))
    assert download_pdf.status_code == 200
    assert download_pdf.headers["content-type"].startswith("application/pdf")
    assert download_pdf.content == pdf_bytes

    # Tipo inválido -> 415
    invalido = cliente.post(
        f"/api/v1/conversas/{conversa.id}/anexos",
        files={"arquivo": ("script.txt", BytesIO(b"texto simples"), "text/plain")},
        headers=cabecalho(cenario.contratante),
    )
    assert invalido.status_code == 415
    assert invalido.json()["detail"]["codigo"] == "formato_nao_suportado"

    # Vazio -> 400
    vazio = cliente.post(
        f"/api/v1/conversas/{conversa.id}/anexos",
        files={"arquivo": ("vazio.png", BytesIO(b""), "image/png")},
        headers=cabecalho(cenario.contratante),
    )
    assert vazio.status_code == 400
    assert vazio.json()["detail"]["codigo"] == "arquivo_vazio"

    # Acima de 10 MB -> 413
    grande = b"\x89PNG\r\n\x1a\n" + b"a" * (10 * 1024 * 1024)
    recusado_tamanho = cliente.post(
        f"/api/v1/conversas/{conversa.id}/anexos",
        files={"arquivo": ("enorme.png", BytesIO(grande), "image/png")},
        headers=cabecalho(cenario.contratante),
    )
    assert recusado_tamanho.status_code == 413
    assert recusado_tamanho.json()["detail"]["codigo"] == "arquivo_muito_grande"

    # Legenda com telefone é bloqueada e NÃO grava o arquivo em disco
    arquivos_antes = list(_pasta_anexos_temporaria.rglob("*"))
    recusado_legenda = cliente.post(
        f"/api/v1/conversas/{conversa.id}/anexos",
        files={"arquivo": ("comprovante.png", BytesIO(png_bytes), "image/png")},
        data={"texto": "Chama no 81.9.9999.9999"},
        headers=cabecalho(cenario.contratante),
    )
    assert recusado_legenda.status_code == 422
    assert recusado_legenda.json()["detail"]["codigo"] == "contato_externo_bloqueado"
    assert list(_pasta_anexos_temporaria.rglob("*")) == arquivos_antes

    # Traversal / inexistente -> 404
    assert (
        cliente.get(
            f"/api/v1/conversas/{conversa.id}/anexos/inexistente.png",
            headers=cabecalho(cenario.contratante),
        ).status_code
        == 404
    )

    # Conversa fechada -> 409
    cenario.vaga.estado = EstadoVaga.CANCELADA
    db.commit()
    fechada = cliente.post(
        f"/api/v1/conversas/{conversa.id}/anexos",
        files={"arquivo": ("local.png", BytesIO(png_bytes), "image/png")},
        headers=cabecalho(cenario.contratante),
    )
    assert fechada.status_code == 409
    assert fechada.json()["detail"]["codigo"] == "conversa_fechada"
