"""Testes da US61: disputa (contestação em 24h), trava na varredura, aba disputas e 3 decisões."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models import (
    Conversa,
    EstadoDisputa,
    EstadoPagamento,
    EstadoVaga,
    Mensagem,
    PapelUsuario,
    TipoPerfil,
)
from app.services import pagamentos
from app.tempo import avancar_relogio, fixar_relogio
from tests.fabricas import cabecalho, criar_cenario_vaga, criar_usuario

BASE = datetime.now(UTC).replace(microsecond=0)


def _cenario_com_pagamento_retido(db, *, valor: Decimal = Decimal("200.00")):
    """Cria uma vaga cujo serviço acabou de terminar, com pagamento Pix retido."""
    fixar_relogio(BASE)
    cenario = criar_cenario_vaga(
        db,
        valor=valor,
        inicio_em=BASE + timedelta(hours=1),
        duracao_minutos=120,
    )
    pagamento = pagamentos.criar_pagamento(db, cenario.vaga)
    pagamentos.reter(db, pagamento)
    # Posiciona o relógio logo após o fim previsto do serviço
    fixar_relogio(pagamentos.fim_do_servico(cenario.vaga) + timedelta(minutes=10))
    return cenario, pagamento


# --- Contestação em 24h e trava da varredura de 72h --------------------------------------


def test_contestacao_em_24h_trava_a_vaga_em_disputa_e_varredura_de_72h_nao_libera(
    cliente, db
):
    cenario, pagamento = _cenario_com_pagamento_retido(db)
    vaga_id = cenario.vaga.id

    resposta = cliente.post(
        f"/api/v1/vagas/{vaga_id}/disputa",
        json={"descricao": "O profissional deixou o trabalho incompleto."},
        headers=cabecalho(cenario.contratante),
    )

    assert resposta.status_code == 201
    corpo = resposta.json()
    assert corpo["vaga_id"] == vaga_id
    assert corpo["vaga_estado"] == "em_disputa"
    assert corpo["estado"] == "aberta"
    assert corpo["aberta_por_id"] == cenario.contratante.id
    assert corpo["pagamento"]["estado"] == "retido"
    assert corpo["pagamento"]["saldo_retido"] == "200.00"

    db.refresh(cenario.vaga)
    assert cenario.vaga.estado == EstadoVaga.EM_DISPUTA

    # Avança mais de 72h após o fim do serviço e roda as varreduras de conclusão e liberação parcial
    avancar_relogio(timedelta(days=5))
    assert pagamentos.concluir_automaticamente(db) == 0
    assert pagamentos.liberar_parcialmente_sem_validacao(db) == 0

    db.refresh(cenario.vaga)
    db.refresh(pagamento)
    assert cenario.vaga.estado == EstadoVaga.EM_DISPUTA
    assert pagamento.estado == EstadoPagamento.RETIDO
    assert pagamentos.saldo_retido(pagamento) == Decimal("200.00")
    assert len(pagamento.lancamentos) == 1

    # Ambos os lados da vaga consultam a disputa
    for participante in (cenario.contratante, cenario.profissional):
        consulta = cliente.get(
            f"/api/v1/vagas/{vaga_id}/disputa",
            headers=cabecalho(participante),
        )
        assert consulta.status_code == 200
        assert consulta.json()["id"] == corpo["id"]


def test_contestacao_recusa_antes_do_fim_apos_24h_duplicada_ou_por_nao_dono(cliente, db):
    fixar_relogio(BASE)
    cenario = criar_cenario_vaga(
        db,
        inicio_em=BASE + timedelta(hours=1),
        duracao_minutos=120,
    )
    pagamento = pagamentos.criar_pagamento(db, cenario.vaga)
    pagamentos.reter(db, pagamento)
    vaga_id = cenario.vaga.id
    outro_contratante = criar_usuario(
        db,
        perfis=(TipoPerfil.CONTRATANTE,),
        perfil_ativo=TipoPerfil.CONTRATANTE,
    )

    # 1. Antes do fim do serviço -> 409 servico_nao_terminou
    cedo = cliente.post(
        f"/api/v1/vagas/{vaga_id}/disputa",
        json={"descricao": "Tentativa antes do fim."},
        headers=cabecalho(cenario.contratante),
    )
    assert cedo.status_code == 409
    assert cedo.json()["detail"]["codigo"] == "servico_nao_terminou"

    # 2. Profissional ou outro contratante não podem abrir disputa
    fim = pagamentos.fim_do_servico(cenario.vaga)
    fixar_relogio(fim + timedelta(minutes=15))
    assert (
        cliente.post(
            f"/api/v1/vagas/{vaga_id}/disputa",
            json={"descricao": "Profissional tentando."},
            headers=cabecalho(cenario.profissional),
        ).status_code
        == 403
    )
    assert (
        cliente.post(
            f"/api/v1/vagas/{vaga_id}/disputa",
            json={"descricao": "Outro contratante."},
            headers=cabecalho(outro_contratante),
        ).status_code
        == 403
    )

    # 3. Após 24h do fim do serviço -> 409 prazo_contestacao_encerrado
    fixar_relogio(fim + timedelta(hours=24, minutes=1))
    tarde = cliente.post(
        f"/api/v1/vagas/{vaga_id}/disputa",
        json={"descricao": "Passou de 24h."},
        headers=cabecalho(cenario.contratante),
    )
    assert tarde.status_code == 409
    assert tarde.json()["detail"]["codigo"] == "prazo_contestacao_encerrado"

    # 4. Dentro do prazo abre, mas segunda tentativa gera 409 disputa_ja_existe
    fixar_relogio(fim + timedelta(hours=2))
    primeira = cliente.post(
        f"/api/v1/vagas/{vaga_id}/disputa",
        json={"descricao": "Dentro das 24h."},
        headers=cabecalho(cenario.contratante),
    )
    assert primeira.status_code == 201
    duplicada = cliente.post(
        f"/api/v1/vagas/{vaga_id}/disputa",
        json={"descricao": "Segunda tentativa."},
        headers=cabecalho(cenario.contratante),
    )
    assert duplicada.status_code == 409
    assert duplicada.json()["detail"]["codigo"] == "disputa_ja_existe"


# --- Aba disputas no admin: chat e anexos da vaga ----------------------------------------


def test_aba_disputas_do_admin_lista_o_chat_e_os_anexos_da_vaga(cliente, db):
    cenario, _ = _cenario_com_pagamento_retido(db)
    admin = criar_usuario(db, papel=PapelUsuario.ADMIN)

    conversa = Conversa(vaga_id=cenario.vaga.id, aberta=True)
    db.add(conversa)
    db.flush()
    db.add_all(
        [
            Mensagem(
                conversa_id=conversa.id,
                autor_id=cenario.profissional.id,
                texto="Iniciei a montagem.",
                anexo_url="/api/v1/arquivos/anexo/antes.jpg",
            ),
            Mensagem(
                conversa_id=conversa.id,
                autor_id=cenario.contratante.id,
                texto="As portas ficaram desalinhadas.",
                anexo_url="/api/v1/arquivos/anexo/depois.png",
            ),
            Mensagem(
                conversa_id=conversa.id,
                autor_id=cenario.contratante.id,
                texto="Vou abrir contestação.",
                anexo_url=None,
            ),
        ]
    )
    db.commit()

    aberta = cliente.post(
        f"/api/v1/vagas/{cenario.vaga.id}/disputa",
        json={"descricao": "Portas desalinhadas conforme fotos no chat."},
        headers=cabecalho(cenario.contratante),
    )
    assert aberta.status_code == 201
    disputa_id = aberta.json()["id"]

    avancar_relogio(timedelta(hours=2))

    # Usuário comum é barrado na aba do admin
    assert (
        cliente.get("/api/v1/admin/disputas", headers=cabecalho(cenario.contratante)).status_code
        == 403
    )

    aba = cliente.get("/api/v1/admin/disputas", headers=cabecalho(admin))
    assert aba.status_code == 200
    itens = aba.json()
    assert len(itens) == 1
    item = itens[0]

    assert item["id"] == disputa_id
    assert item["vaga_id"] == cenario.vaga.id
    assert item["conversa_id"] == conversa.id
    assert item["tempo_espera_minutos"] == 120
    assert item["meta_horas_uteis"] == 48
    assert len(item["chat"]) == 3
    assert [m["texto"] for m in item["chat"]] == [
        "Iniciei a montagem.",
        "As portas ficaram desalinhadas.",
        "Vou abrir contestação.",
    ]
    assert item["anexos"] == [
        "/api/v1/arquivos/anexo/antes.jpg",
        "/api/v1/arquivos/anexo/depois.png",
    ]

    detalhe = cliente.get(f"/api/v1/admin/disputas/{disputa_id}", headers=cabecalho(admin))
    assert detalhe.status_code == 200
    assert detalhe.json() == item


# --- Decisão do admin com os 3 resultados e lançamentos no ledger ------------------------


def test_decisao_reembolso_total_estorna_integral_e_registra_motivo(cliente, db):
    cenario, pagamento = _cenario_com_pagamento_retido(db, valor=Decimal("200.00"))
    admin = criar_usuario(db, papel=PapelUsuario.ADMIN)
    disputa_id = cliente.post(
        f"/api/v1/vagas/{cenario.vaga.id}/disputa",
        json={"descricao": "Serviço não foi realizado."},
        headers=cabecalho(cenario.contratante),
    ).json()["id"]

    decisao = cliente.post(
        f"/api/v1/admin/disputas/{disputa_id}/decisao",
        json={
            "decisao": "reembolso_total",
            "motivo": "Comprovado que o serviço não foi executado.",
        },
        headers=cabecalho(admin),
    )

    assert decisao.status_code == 200
    corpo = decisao.json()
    assert corpo["estado"] == "reembolso_total"
    assert corpo["percentual_reembolso"] == 100
    assert corpo["decisao_motivo"] == "Comprovado que o serviço não foi executado."
    assert corpo["decidido_em"] is not None
    assert corpo["vaga_estado"] == "reembolsada"
    assert corpo["pagamento"]["estado"] == "estornado"
    assert corpo["pagamento"]["saldo_retido"] == "0.00"
    assert [(item["tipo"], item["valor"]) for item in corpo["pagamento"]["lancamentos"]] == [
        ("retencao", "200.00"),
        ("estorno", "200.00"),
    ]

    db.refresh(pagamento)
    assert pagamentos.saldo_retido(pagamento) == Decimal("0.00")


def test_decisao_reembolso_parcial_libera_parcela_e_estorna_o_resto(cliente, db):
    # Valor 200,00; reembolso de 40% (80,00 estornado) e 60% liberado (120,00 bruto ->
    # 108,00 líquido ao profissional + 12,00 taxa de 10% da plataforma).
    cenario, pagamento = _cenario_com_pagamento_retido(db, valor=Decimal("200.00"))
    admin = criar_usuario(db, papel=PapelUsuario.ADMIN)
    disputa_id = cliente.post(
        f"/api/v1/vagas/{cenario.vaga.id}/disputa",
        json={"descricao": "Apenas 60% do serviço foi concluído."},
        headers=cabecalho(cenario.contratante),
    ).json()["id"]

    decisao = cliente.post(
        f"/api/v1/admin/disputas/{disputa_id}/decisao",
        json={
            "decisao": "reembolso_parcial",
            "percentual_reembolso": 40,
            "motivo": "60% executado e 40% pendente segundo as fotos anexadas.",
        },
        headers=cabecalho(admin),
    )

    assert decisao.status_code == 200
    corpo = decisao.json()
    assert corpo["estado"] == "reembolso_parcial"
    assert corpo["percentual_reembolso"] == 40
    assert corpo["decisao_motivo"] == "60% executado e 40% pendente segundo as fotos anexadas."
    assert corpo["vaga_estado"] == "liberada_parcial"
    assert corpo["pagamento"]["estado"] == "liberado_parcial"
    assert corpo["pagamento"]["saldo_retido"] == "0.00"
    assert [(item["tipo"], item["valor"]) for item in corpo["pagamento"]["lancamentos"]] == [
        ("retencao", "200.00"),
        ("liberacao_profissional", "108.00"),
        ("taxa_plataforma", "12.00"),
        ("estorno", "80.00"),
    ]

    db.refresh(pagamento)
    assert pagamentos.saldo_retido(pagamento) == Decimal("0.00")


def test_decisao_pagamento_integral_libera_tudo_ao_profissional_e_impede_redecisao(
    cliente, db
):
    cenario, pagamento = _cenario_com_pagamento_retido(db, valor=Decimal("200.00"))
    admin = criar_usuario(db, papel=PapelUsuario.ADMIN)
    disputa_id = cliente.post(
        f"/api/v1/vagas/{cenario.vaga.id}/disputa",
        json={"descricao": "Contestação sem fundamento."},
        headers=cabecalho(cenario.contratante),
    ).json()["id"]

    decisao = cliente.post(
        f"/api/v1/admin/disputas/{disputa_id}/decisao",
        json={
            "decisao": "pagamento_integral",
            "motivo": "O serviço foi entregue integralmente conforme combinado no chat.",
        },
        headers=cabecalho(admin),
    )

    assert decisao.status_code == 200
    corpo = decisao.json()
    assert corpo["estado"] == EstadoDisputa.PAGAMENTO_INTEGRAL.value
    assert corpo["percentual_reembolso"] == 0
    assert (
        corpo["decisao_motivo"]
        == "O serviço foi entregue integralmente conforme combinado no chat."
    )
    assert corpo["vaga_estado"] == "liberada"
    assert corpo["pagamento"]["estado"] == "liberado"
    assert corpo["pagamento"]["saldo_retido"] == "0.00"
    assert [(item["tipo"], item["valor"]) for item in corpo["pagamento"]["lancamentos"]] == [
        ("retencao", "200.00"),
        ("liberacao_profissional", "180.00"),
        ("taxa_plataforma", "20.00"),
    ]

    db.refresh(pagamento)
    assert pagamentos.saldo_retido(pagamento) == Decimal("0.00")

    # Tentar decidir novamente a mesma disputa retorna 409 disputa_ja_decidida
    repetida = cliente.post(
        f"/api/v1/admin/disputas/{disputa_id}/decisao",
        json={"decisao": "reembolso_total", "motivo": "Tentativa duplicada."},
        headers=cabecalho(admin),
    )
    assert repetida.status_code == 409
    assert repetida.json()["detail"]["codigo"] == "disputa_ja_decidida"
