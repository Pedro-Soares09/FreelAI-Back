"""Testes da US62 (PAG05): central de notificações, atraso por plano (Pro vs +2 min) e leitura."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.api.v1.schemas.notificacoes import EventoNotificacao
from app.config import settings
from app.models import (
    Configuracao,
    Denuncia,
    EstadoDenuncia,
    Notificacao,
    OrigemDenuncia,
)
from app.services import match, notificacoes, pagamentos, varreduras
from app.tempo import avancar_relogio, como_utc, fixar_relogio
from tests.fabricas import (
    cabecalho,
    criar_categoria,
    criar_cenario_vaga,
    criar_profissional,
    criar_usuario,
    criar_vaga,
)

BASE = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


# --- 9 eventos da lista geram notificação ao destinatário --------------------------------


def test_cada_um_dos_9_eventos_gera_notificacao_ao_destinatario(db):
    fixar_relogio(BASE)
    categoria = criar_categoria(db)
    profissional_perfil = criar_profissional(db, categorias=(categoria,))
    profissional_usuario = profissional_perfil.usuario
    vaga_publicada = criar_vaga(db, categoria=categoria)

    # 1. Selecionado nos 3 (gerado automaticamente por match.gerar_rodada)
    convites = match.gerar_rodada(db, vaga_publicada)
    assert len(convites) == 1

    # Cenário de contratação para os demais eventos
    cenario = criar_cenario_vaga(
        db,
        inicio_em=BASE + timedelta(hours=1),
        duracao_minutos=60,
    )
    contratante = cenario.contratante
    profissional = cenario.profissional

    # 2. Escolhido (convite)
    notificacoes.notificar_convite_escolhido(db, cenario.vaga, profissional.id)

    # 3. Aceite do convite
    notificacoes.notificar_convite_aceito(db, cenario.vaga)

    # 4. Contrato pronto
    notificacoes.notificar_contrato_pronto(db, cenario.vaga)

    # 5. Pagamento retido (gerado automaticamente por pagamentos.reter)
    pagamento = pagamentos.criar_pagamento(db, cenario.vaga)
    pagamentos.reter(db, pagamento)

    # 6. Lembrete de confirmação (gerado pela varredura após o fim do serviço)
    fixar_relogio(pagamentos.fim_do_servico(cenario.vaga) + timedelta(minutes=1))
    assert "lembretes_confirmacao" in varreduras.nomes_registrados()
    assert notificacoes.varrer_lembretes_de_confirmacao(db) == 1
    assert notificacoes.varrer_lembretes_de_confirmacao(db) == 0

    # 8. Avaliação pendente (gerado automaticamente por pagamentos.confirmar_conclusao)
    pagamentos.confirmar_conclusao(db, cenario.vaga, contratante.id)

    # 7. Liberação do pagamento (gerado automaticamente por pagamentos.liberar_integral)
    pagamentos.liberar_integral(db, pagamento)

    # 9. Denúncia decidida
    denuncia = Denuncia(
        denunciante_id=contratante.id,
        denunciado_id=profissional.id,
        vaga_id=cenario.vaga.id,
        origem=OrigemDenuncia.USUARIO,
        motivo="atraso",
        estado=EstadoDenuncia.IMPROCEDENTE,
    )
    db.add(denuncia)
    db.commit()
    db.refresh(denuncia)
    notificacoes.notificar_denuncia_decidida(db, denuncia)

    registros = db.scalars(select(Notificacao)).all()
    tipos_emitidos = {item.tipo for item in registros}

    assert tipos_emitidos == {evento.value for evento in EventoNotificacao}

    # Confere os destinatários de cada evento
    por_tipo: dict[str, set[int]] = {}
    for item in registros:
        por_tipo.setdefault(item.tipo, set()).add(item.usuario_id)

    assert por_tipo[EventoNotificacao.SELECIONADO_TOP3.value] == {profissional_usuario.id}
    assert por_tipo[EventoNotificacao.CONVITE_ESCOLHIDO.value] == {profissional.id}
    assert por_tipo[EventoNotificacao.CONVITE_ACEITO.value] == {contratante.id}
    assert por_tipo[EventoNotificacao.CONTRATO_PRONTO.value] == {contratante.id, profissional.id}
    assert por_tipo[EventoNotificacao.PAGAMENTO_RETIDO.value] == {profissional.id}
    assert por_tipo[EventoNotificacao.LEMBRETE_CONFIRMACAO.value] == {contratante.id}
    assert por_tipo[EventoNotificacao.PAGAMENTO_LIBERADO.value] == {profissional.id}
    assert por_tipo[EventoNotificacao.AVALIACAO_PENDENTE.value] == {
        contratante.id,
        profissional.id,
    }
    assert por_tipo[EventoNotificacao.DENUNCIA_DECIDIDA.value] == {
        contratante.id,
        profissional.id,
    }


# --- Atraso por plano: Pro imediato vs +2 min (configurável; 0 em demo) ------------------


def test_quem_nao_e_pro_so_ve_a_notificacao_apos_o_atraso_e_pro_ve_na_hora(cliente, db):
    fixar_relogio(BASE)
    comum = criar_usuario(db)
    assinante_pro = criar_usuario(db)
    notificacoes.definir_plano_pro(assinante_pro.id, True)

    notif_comum = notificacoes.criar_notificacao(
        db,
        usuario_id=comum.id,
        tipo=EventoNotificacao.SELECIONADO_TOP3,
        titulo="Nova vaga perto de você",
        mensagem="Você entrou no top 3.",
    )
    notif_pro = notificacoes.criar_notificacao(
        db,
        usuario_id=assinante_pro.id,
        tipo=EventoNotificacao.SELECIONADO_TOP3,
        titulo="Nova vaga perto de você",
        mensagem="Você entrou no top 3.",
    )

    assert como_utc(notif_pro.visivel_em) == BASE
    assert como_utc(notif_comum.visivel_em) == BASE + timedelta(minutes=2)

    # No instante 0: Pro já vê a notificação; usuário comum ainda não vê nada
    central_pro = cliente.get("/api/v1/notificacoes", headers=cabecalho(assinante_pro)).json()
    assert central_pro["nao_lidas"] == 1
    assert len(central_pro["itens"]) == 1

    central_comum_t0 = cliente.get("/api/v1/notificacoes", headers=cabecalho(comum)).json()
    assert central_comum_t0["nao_lidas"] == 0
    assert central_comum_t0["itens"] == []

    # Tentar marcar como lida antes de ficar visível retorna 404
    assert (
        cliente.post(
            f"/api/v1/notificacoes/{notif_comum.id}/lida",
            headers=cabecalho(comum),
        ).status_code
        == 404
    )

    # Após 1min59s: usuário comum ainda não vê
    avancar_relogio(timedelta(minutes=1, seconds=59))
    assert (
        cliente.get("/api/v1/notificacoes/nao-lidas", headers=cabecalho(comum)).json()["nao_lidas"]
        == 0
    )

    # Ao completar 2 minutos: usuário comum passa a ver a notificação
    avancar_relogio(timedelta(seconds=1))
    central_comum_t2 = cliente.get("/api/v1/notificacoes", headers=cabecalho(comum)).json()
    assert central_comum_t2["nao_lidas"] == 1
    assert len(central_comum_t2["itens"]) == 1
    assert central_comum_t2["itens"][0]["id"] == notif_comum.id


def test_atraso_respeita_configuracao_do_banco_e_e_zero_no_perfil_demo(
    cliente, db, monkeypatch
):
    fixar_relogio(BASE)
    usuario = criar_usuario(db)

    # 1. Sobrepondo `notificacao_atraso_min` para 5 minutos no banco
    db.add(
        Configuracao(
            chave="notificacao_atraso_min",
            perfil="producao",
            valor="5",
            descricao="5 min",
        )
    )
    db.commit()

    n1 = notificacoes.criar_notificacao(
        db,
        usuario_id=usuario.id,
        tipo=EventoNotificacao.CONVITE_ESCOLHIDO,
        titulo="Convite recebido",
    )
    assert como_utc(n1.visivel_em) == BASE + timedelta(minutes=5)

    # 2. No perfil demo o atraso padrão é 0 min (visível imediatamente mesmo para quem não é Pro)
    monkeypatch.setattr(settings, "perfil", "demo")
    n2 = notificacoes.criar_notificacao(
        db,
        usuario_id=usuario.id,
        tipo=EventoNotificacao.CONTRATO_PRONTO,
        titulo="Contrato pronto em demo",
    )
    assert como_utc(n2.visivel_em) == BASE

    central = cliente.get("/api/v1/notificacoes", headers=cabecalho(usuario)).json()
    assert [item["id"] for item in central["itens"]] == [n2.id]


# --- Marcação de leitura e contagem de não lidas -----------------------------------------


def test_marcar_como_lida_individual_e_em_lote_atualiza_contagem_de_nao_lidas(cliente, db):
    fixar_relogio(BASE)
    usuario = criar_usuario(db)
    outro = criar_usuario(db)

    # Cria 3 notificações e avança os 2 minutos para todas ficarem visíveis
    criadas = [
        notificacoes.criar_notificacao(
            db,
            usuario_id=usuario.id,
            tipo=EventoNotificacao.SELECIONADO_TOP3,
            titulo=f"Alerta {i}",
        )
        for i in range(1, 4)
    ]
    avancar_relogio(timedelta(minutes=2))

    # Cria uma 4ª notificação agora (ainda ficará oculta pelos próximos 2 min)
    oculta = notificacoes.criar_notificacao(
        db,
        usuario_id=usuario.id,
        tipo=EventoNotificacao.PAGAMENTO_LIBERADO,
        titulo="Ainda oculta",
    )

    contagem = cliente.get("/api/v1/notificacoes/nao-lidas", headers=cabecalho(usuario))
    assert contagem.status_code == 200
    assert contagem.json()["nao_lidas"] == 3

    # Outro usuário não pode marcar a notificação alheia como lida
    proibido = cliente.post(
        f"/api/v1/notificacoes/{criadas[0].id}/lida",
        headers=cabecalho(outro),
    )
    assert proibido.status_code == 403
    assert proibido.json()["detail"]["codigo"] == "notificacao_de_outro_usuario"

    # Marca a primeira como lida
    lida1 = cliente.post(
        f"/api/v1/notificacoes/{criadas[0].id}/lida",
        headers=cabecalho(usuario),
    )
    assert lida1.status_code == 200
    assert lida1.json()["lida"] is True
    assert lida1.json()["lida_em"] is not None

    # Contagem de não lidas cai para 2 e filtro `apenas_nao_lidas=true` traz só 2
    apenas_nao_lidas = cliente.get(
        "/api/v1/notificacoes?apenas_nao_lidas=true",
        headers=cabecalho(usuario),
    ).json()
    assert apenas_nao_lidas["nao_lidas"] == 2
    assert len(apenas_nao_lidas["itens"]) == 2

    # Marca todas as visíveis restantes como lidas em lote
    lote = cliente.post("/api/v1/notificacoes/ler-todas", headers=cabecalho(usuario))
    assert lote.status_code == 200
    assert lote.json()["marcadas"] == 2

    restantes = cliente.get(
        "/api/v1/notificacoes/nao-lidas",
        headers=cabecalho(usuario),
    ).json()
    assert restantes["nao_lidas"] == 0

    # A 4ª notificação (que estava oculta) não foi marcada antes de ficar visível;
    # ao avançar mais 2 min, ela aparece como não lida
    avancar_relogio(timedelta(minutes=2))
    central_final = cliente.get("/api/v1/notificacoes", headers=cabecalho(usuario)).json()
    assert central_final["nao_lidas"] == 1
    assert central_final["itens"][0]["id"] == oculta.id
    assert central_final["itens"][0]["lida"] is False
