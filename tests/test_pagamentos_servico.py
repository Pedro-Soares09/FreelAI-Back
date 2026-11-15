from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models import (
    Disputa,
    EstadoPagamento,
    EstadoVaga,
    Lancamento,
    TipoLancamento,
)
from app.services import configuracao, pagamentos, varreduras
from app.tempo import agora, avancar_relogio, como_utc, fixar_relogio
from tests.fabricas import criar_cenario_vaga

BASE = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def _minutos(chave: str, db) -> timedelta:
    return timedelta(minutes=configuracao.get(chave, db=db))


def _retido(db, **opcoes):
    """Vaga com pagamento já retido, com o relógio fixado em BASE."""
    fixar_relogio(BASE)
    cenario = criar_cenario_vaga(db, inicio_em=BASE + timedelta(hours=1), **opcoes)
    pagamento = pagamentos.criar_pagamento(db, cenario.vaga)
    pagamentos.reter(db, pagamento)
    return cenario, pagamento


def _valores(pagamento, tipo: TipoLancamento) -> list[Decimal]:
    return [item.valor for item in pagamento.lancamentos if item.tipo == tipo]


def _conferir_livro_fechado(pagamento):
    saidas = sum(
        (item.valor for item in pagamento.lancamentos if item.tipo != TipoLancamento.RETENCAO),
        Decimal("0"),
    )
    assert sum(_valores(pagamento, TipoLancamento.RETENCAO), Decimal("0")) == pagamento.valor_total
    assert saidas == pagamento.valor_total
    assert pagamentos.saldo_retido(pagamento) == Decimal("0.00")


# Divisão


def test_divisao_de_150_com_dez_por_cento():
    assert pagamentos.calcular_divisao(Decimal("150.00"), 10) == (
        Decimal("135.00"),
        Decimal("15.00"),
    )


@pytest.mark.parametrize(
    ("total", "liquido", "taxa"),
    [
        ("33.33", "30.00", "3.33"),
        ("33.35", "30.01", "3.34"),
        ("0.05", "0.04", "0.01"),
        ("0.04", "0.04", "0.00"),
        ("999.99", "899.99", "100.00"),
    ],
)
def test_divisao_arredonda_a_taxa_para_centavos_sem_residuo(total, liquido, taxa):
    resultado = pagamentos.calcular_divisao(Decimal(total), 10)

    assert resultado == (Decimal(liquido), Decimal(taxa))
    assert resultado[0] + resultado[1] == Decimal(total)


def test_divisao_usa_a_taxa_configurada_quando_nao_informada(db):
    assert pagamentos.calcular_divisao(Decimal("150.00"), db=db) == (
        Decimal("135.00"),
        Decimal("15.00"),
    )
    assert isinstance(pagamentos.taxa_configurada(db), Decimal)


# Criação e retenção


def test_criar_pagamento_usa_valor_fixado_e_prazo_configurado(db):
    fixar_relogio(BASE)
    cenario = criar_cenario_vaga(db, valor=Decimal("150.00"), valor_fixado=Decimal("180.00"))

    pagamento = pagamentos.criar_pagamento(db, cenario.vaga)

    assert pagamento.valor_total == Decimal("180.00")
    assert pagamento.estado == EstadoPagamento.AGUARDANDO
    assert pagamento.codigo_pix.startswith("PIX-SIM-")
    assert como_utc(pagamento.expira_em) == BASE + _minutos("prazo_pagamento_min", db)
    assert cenario.vaga.estado == EstadoVaga.AGUARDANDO_PAGAMENTO
    assert pagamento.lancamentos == []


def test_codigo_pix_simulado_e_deterministico():
    gateway = pagamentos.GatewaySimulado()

    instante = agora()
    primeiro = gateway.criar_cobranca(7, Decimal("10.00"), instante)
    segundo = gateway.criar_cobranca(7, Decimal("10.00"), instante)

    assert primeiro.codigo_pix == segundo.codigo_pix
    assert gateway.criar_cobranca(8, Decimal("10.00"), instante).codigo_pix != primeiro.codigo_pix
    outro_prazo = gateway.criar_cobranca(7, Decimal("10.00"), instante + timedelta(minutes=5))
    assert outro_prazo.codigo_pix != primeiro.codigo_pix


def test_criar_pagamento_recusa_vaga_fora_do_aceite(db):
    cenario = criar_cenario_vaga(db, estado=EstadoVaga.PUBLICADA)

    with pytest.raises(HTTPException) as erro:
        pagamentos.criar_pagamento(db, cenario.vaga)

    assert erro.value.status_code == 409
    assert erro.value.detail["codigo"] == "vaga_nao_aceita"


def test_criar_pagamento_recusa_segunda_cobranca(db):
    cenario = criar_cenario_vaga(db)
    pagamentos.criar_pagamento(db, cenario.vaga)

    with pytest.raises(HTTPException) as erro:
        pagamentos.criar_pagamento(db, cenario.vaga)

    assert erro.value.status_code == 409
    assert erro.value.detail["codigo"] == "pagamento_ja_existe"


def test_reter_grava_retencao_e_contrata_a_vaga(db):
    cenario, pagamento = _retido(db)

    assert pagamento.estado == EstadoPagamento.RETIDO
    assert cenario.vaga.estado == EstadoVaga.CONTRATADA
    assert _valores(pagamento, TipoLancamento.RETENCAO) == [Decimal("150.00")]
    assert pagamentos.saldo_retido(pagamento) == Decimal("150.00")


def test_reter_e_idempotente_e_recusa_cobranca_expirada(db):
    cenario, pagamento = _retido(db)
    pagamentos.reter(db, pagamento)
    assert len(pagamento.lancamentos) == 1

    outro = criar_cenario_vaga(db)
    pendente = pagamentos.criar_pagamento(db, outro.vaga)
    avancar_relogio(_minutos("prazo_pagamento_min", db))
    with pytest.raises(HTTPException) as erro:
        pagamentos.reter(db, pendente)

    assert erro.value.status_code == 409
    assert erro.value.detail["codigo"] == "pagamento_expirado"
    assert pendente.estado == EstadoPagamento.EXPIRADO
    assert outro.vaga.estado == EstadoVaga.PUBLICADA


# Liberações e estorno


def test_liberacao_integral_divide_entre_profissional_e_plataforma(db):
    cenario, pagamento = _retido(db)

    pagamentos.liberar_integral(db, pagamento)

    assert pagamento.estado == EstadoPagamento.LIBERADO
    assert cenario.vaga.estado == EstadoVaga.LIBERADA
    assert _valores(pagamento, TipoLancamento.LIBERACAO_PROFISSIONAL) == [Decimal("135.00")]
    assert _valores(pagamento, TipoLancamento.TAXA_PLATAFORMA) == [Decimal("15.00")]
    assert _valores(pagamento, TipoLancamento.ESTORNO) == []
    _conferir_livro_fechado(pagamento)


def test_liberacao_parcial_de_metade(db):
    cenario, pagamento = _retido(db)

    pagamentos.liberar_parcial(db, pagamento, 50)

    assert pagamento.estado == EstadoPagamento.LIBERADO_PARCIAL
    assert cenario.vaga.estado == EstadoVaga.LIBERADA_PARCIAL
    assert _valores(pagamento, TipoLancamento.LIBERACAO_PROFISSIONAL) == [Decimal("67.50")]
    assert _valores(pagamento, TipoLancamento.TAXA_PLATAFORMA) == [Decimal("7.50")]
    assert _valores(pagamento, TipoLancamento.ESTORNO) == [Decimal("75.00")]
    _conferir_livro_fechado(pagamento)


def test_liberacao_parcial_recusa_percentual_fora_da_faixa(db):
    _, pagamento = _retido(db)

    with pytest.raises(ValueError):
        pagamentos.liberar_parcial(db, pagamento, 101)
    assert pagamento.estado == EstadoPagamento.RETIDO


def test_estorno_integral_devolve_tudo_sem_taxa(db):
    cenario, pagamento = _retido(db)

    pagamentos.estornar_integral(db, pagamento)

    assert pagamento.estado == EstadoPagamento.ESTORNADO
    assert cenario.vaga.estado == EstadoVaga.REEMBOLSADA
    assert _valores(pagamento, TipoLancamento.ESTORNO) == [Decimal("150.00")]
    assert _valores(pagamento, TipoLancamento.TAXA_PLATAFORMA) == []
    _conferir_livro_fechado(pagamento)


@pytest.mark.parametrize(
    ("operacao", "estado_final"),
    [
        (lambda db, p: pagamentos.liberar_integral(db, p), EstadoPagamento.LIBERADO),
        (lambda db, p: pagamentos.liberar_parcial(db, p, 50), EstadoPagamento.LIBERADO_PARCIAL),
        (lambda db, p: pagamentos.estornar_integral(db, p), EstadoPagamento.ESTORNADO),
    ],
)
def test_repetir_a_mesma_operacao_nao_duplica_lancamentos(db, operacao, estado_final):
    _, pagamento = _retido(db)
    operacao(db, pagamento)
    quantidade = len(pagamento.lancamentos)

    operacao(db, pagamento)

    assert pagamento.estado == estado_final
    assert len(pagamento.lancamentos) == quantidade
    _conferir_livro_fechado(pagamento)


@pytest.mark.parametrize(
    ("primeira", "segunda"),
    [
        (
            lambda db, p: pagamentos.liberar_integral(db, p),
            lambda db, p: pagamentos.estornar_integral(db, p),
        ),
        (
            lambda db, p: pagamentos.estornar_integral(db, p),
            lambda db, p: pagamentos.liberar_integral(db, p),
        ),
        (
            lambda db, p: pagamentos.liberar_parcial(db, p, 50),
            lambda db, p: pagamentos.liberar_integral(db, p),
        ),
        (
            lambda db, p: pagamentos.liberar_integral(db, p),
            lambda db, p: pagamentos.liberar_parcial(db, p, 50),
        ),
    ],
)
def test_operacao_conflitante_sobre_estado_final_gera_409(db, primeira, segunda):
    _, pagamento = _retido(db)
    primeira(db, pagamento)
    quantidade = len(pagamento.lancamentos)

    with pytest.raises(HTTPException) as erro:
        segunda(db, pagamento)

    assert erro.value.status_code == 409
    assert erro.value.detail["codigo"] == "pagamento_estado_invalido"
    assert len(pagamento.lancamentos) == quantidade
    _conferir_livro_fechado(pagamento)


def test_liberacoes_exigem_valor_retido(db):
    cenario = criar_cenario_vaga(db)
    pagamento = pagamentos.criar_pagamento(db, cenario.vaga)

    for operacao in (
        lambda: pagamentos.liberar_integral(db, pagamento),
        lambda: pagamentos.liberar_parcial(db, pagamento, 50),
        lambda: pagamentos.estornar_integral(db, pagamento),
    ):
        with pytest.raises(HTTPException) as erro:
            operacao()
        assert erro.value.status_code == 409
    assert pagamento.lancamentos == []


@pytest.mark.parametrize("total", ["33.33", "0.01", "1234.56", "99.99"])
def test_invariante_do_livro_vale_para_qualquer_valor(db, total):
    cenarios = [_retido(db, valor=Decimal(total)) for _ in range(3)]

    pagamentos.liberar_integral(db, cenarios[0][1])
    pagamentos.liberar_parcial(db, cenarios[1][1], 50)
    pagamentos.estornar_integral(db, cenarios[2][1])

    for _, pagamento in cenarios:
        _conferir_livro_fechado(pagamento)


def test_lancamentos_do_servico_sao_imutaveis(db):
    _, pagamento = _retido(db)
    pagamentos.liberar_integral(db, pagamento)
    lancamento = db.scalar(
        select(Lancamento).where(
            Lancamento.pagamento_id == pagamento.id,
            Lancamento.tipo == TipoLancamento.TAXA_PLATAFORMA,
        )
    )

    lancamento.valor = Decimal("0.01")
    with pytest.raises(RuntimeError):
        db.commit()
    db.rollback()

    db.delete(lancamento)
    with pytest.raises(RuntimeError):
        db.commit()
    db.rollback()
    db.refresh(pagamento)
    _conferir_livro_fechado(pagamento)


# Conclusão, validação facial e falta


def test_conclusao_so_depois_do_fim_do_servico(db):
    cenario, _ = _retido(db)
    contratante_id = cenario.contratante.id

    with pytest.raises(HTTPException) as erro:
        pagamentos.confirmar_conclusao(db, cenario.vaga, contratante_id)
    assert erro.value.detail["codigo"] == "servico_nao_terminou"

    fixar_relogio(pagamentos.fim_do_servico(cenario.vaga))
    pagamentos.confirmar_conclusao(db, cenario.vaga, contratante_id)

    assert cenario.vaga.estado == EstadoVaga.CONCLUIDA


def test_conclusao_recusa_quem_nao_e_o_contratante(db):
    cenario, _ = _retido(db)
    fixar_relogio(pagamentos.fim_do_servico(cenario.vaga))

    with pytest.raises(HTTPException) as erro:
        pagamentos.confirmar_conclusao(db, cenario.vaga, cenario.profissional.id)

    assert erro.value.status_code == 403


def test_validacao_facial_exige_conclusao_e_libera_integral(db):
    cenario, pagamento = _retido(db)

    with pytest.raises(HTTPException) as erro:
        pagamentos.validar_facial_e_liberar(db, cenario.vaga, cenario.contratante)
    assert erro.value.status_code == 409
    assert erro.value.detail["codigo"] == "vaga_nao_concluida"

    fixar_relogio(pagamentos.fim_do_servico(cenario.vaga))
    pagamentos.confirmar_conclusao(db, cenario.vaga, cenario.contratante.id)
    pagamentos.validar_facial_e_liberar(db, cenario.vaga, cenario.contratante)

    assert pagamento.estado == EstadoPagamento.LIBERADO
    assert cenario.vaga.estado == EstadoVaga.LIBERADA
    _conferir_livro_fechado(pagamento)


def test_validacao_facial_reprovada_nao_libera(db, monkeypatch):
    cenario, pagamento = _retido(db)
    fixar_relogio(pagamentos.fim_do_servico(cenario.vaga))
    pagamentos.confirmar_conclusao(db, cenario.vaga, cenario.contratante.id)

    class Reprova:
        def validar(self, db, usuario_id, *, selfie_url=None):
            return False

    monkeypatch.setattr(pagamentos.biometria, "obter_reconhecimento", lambda: Reprova())
    with pytest.raises(HTTPException) as erro:
        pagamentos.validar_facial_e_liberar(db, cenario.vaga, cenario.contratante)

    assert erro.value.status_code == 403
    assert pagamento.estado == EstadoPagamento.RETIDO
    assert pagamentos.saldo_retido(pagamento) == Decimal("150.00")


def test_falta_do_profissional_estorna_tudo(db):
    cenario, pagamento = _retido(db)
    fixar_relogio(como_utc(cenario.vaga.inicio_em) + _minutos("tolerancia_falta_min", db))

    pagamentos.registrar_falta(db, cenario.vaga)

    assert cenario.vaga.estado == EstadoVaga.FALTA
    assert pagamento.estado == EstadoPagamento.ESTORNADO
    assert _valores(pagamento, TipoLancamento.ESTORNO) == [Decimal("150.00")]
    _conferir_livro_fechado(pagamento)

    pagamentos.registrar_falta(db, cenario.vaga)
    assert len(pagamento.lancamentos) == 2


# Varreduras


def test_varreduras_do_pagamento_estao_registradas():
    assert {"conclusao_automatica", "liberacao_parcial", "expiracao_pagamentos"} <= set(
        varreduras.nomes_registrados()
    )


def test_expiracao_devolve_a_vaga_a_publicacao(db):
    fixar_relogio(BASE)
    cenario = criar_cenario_vaga(db)
    pagamento = pagamentos.criar_pagamento(db, cenario.vaga)

    assert pagamentos.expirar_pagamentos(db) == 0
    avancar_relogio(_minutos("prazo_pagamento_min", db))
    assert pagamentos.expirar_pagamentos(db) == 1
    assert pagamentos.expirar_pagamentos(db) == 0

    db.refresh(pagamento)
    db.refresh(cenario.vaga)
    assert pagamento.estado == EstadoPagamento.EXPIRADO
    assert cenario.vaga.estado == EstadoVaga.PUBLICADA
    assert cenario.vaga.profissional_id is None
    assert pagamento.lancamentos == []


def test_cobranca_expirada_pode_ser_reaberta_depois_de_novo_aceite(db):
    fixar_relogio(BASE)
    cenario = criar_cenario_vaga(db)
    antigo = pagamentos.criar_pagamento(db, cenario.vaga)
    avancar_relogio(_minutos("prazo_pagamento_min", db))
    pagamentos.expirar_pagamentos(db)
    cenario.vaga.estado = EstadoVaga.ACEITA
    cenario.vaga.profissional_id = cenario.profissional.perfil_profissional.id
    db.commit()

    novo = pagamentos.criar_pagamento(db, cenario.vaga)

    assert novo.id == antigo.id
    assert novo.estado == EstadoPagamento.AGUARDANDO
    assert como_utc(novo.expira_em) == agora() + _minutos("prazo_pagamento_min", db)


def test_conclusao_automatica_apos_o_prazo_de_confirmacao(db):
    cenario, _ = _retido(db)
    fim = pagamentos.fim_do_servico(cenario.vaga)

    fixar_relogio(fim + _minutos("confirmacao_conclusao_min", db) - timedelta(seconds=1))
    assert pagamentos.concluir_automaticamente(db) == 0
    assert cenario.vaga.estado == EstadoVaga.CONTRATADA

    avancar_relogio(timedelta(seconds=1))
    assert pagamentos.concluir_automaticamente(db) == 1
    assert pagamentos.concluir_automaticamente(db) == 0
    db.refresh(cenario.vaga)
    assert cenario.vaga.estado == EstadoVaga.CONCLUIDA


def test_liberacao_parcial_automatica_sem_validacao_facial(db):
    cenario, pagamento = _retido(db)
    fim = pagamentos.fim_do_servico(cenario.vaga)
    fixar_relogio(fim + _minutos("confirmacao_conclusao_min", db))
    pagamentos.concluir_automaticamente(db)

    fixar_relogio(fim + _minutos("liberacao_parcial_min", db) - timedelta(seconds=1))
    assert pagamentos.liberar_parcialmente_sem_validacao(db) == 0

    avancar_relogio(timedelta(seconds=1))
    assert pagamentos.liberar_parcialmente_sem_validacao(db) == 1
    assert pagamentos.liberar_parcialmente_sem_validacao(db) == 0
    db.refresh(pagamento)
    db.refresh(cenario.vaga)
    assert pagamento.estado == EstadoPagamento.LIBERADO_PARCIAL
    assert cenario.vaga.estado == EstadoVaga.LIBERADA_PARCIAL
    assert _valores(pagamento, TipoLancamento.LIBERACAO_PROFISSIONAL) == [Decimal("67.50")]
    assert _valores(pagamento, TipoLancamento.TAXA_PLATAFORMA) == [Decimal("7.50")]
    assert _valores(pagamento, TipoLancamento.ESTORNO) == [Decimal("75.00")]
    _conferir_livro_fechado(pagamento)


def test_vaga_em_disputa_nunca_e_liberada_automaticamente(db):
    cenario, pagamento = _retido(db)
    fim = pagamentos.fim_do_servico(cenario.vaga)
    fixar_relogio(fim + _minutos("confirmacao_conclusao_min", db))
    pagamentos.concluir_automaticamente(db)
    cenario.vaga.estado = EstadoVaga.EM_DISPUTA
    db.add(Disputa(vaga_id=cenario.vaga.id, aberta_por_id=cenario.contratante.id, descricao="x"))
    db.commit()

    fixar_relogio(fim + _minutos("liberacao_parcial_min", db) + timedelta(days=30))
    assert pagamentos.liberar_parcialmente_sem_validacao(db) == 0
    assert pagamentos.concluir_automaticamente(db) == 0

    db.refresh(pagamento)
    assert pagamento.estado == EstadoPagamento.RETIDO
    assert pagamentos.saldo_retido(pagamento) == Decimal("150.00")


def test_liberacao_parcial_automatica_ignora_vaga_concluida_com_disputa_aberta(db):
    cenario, pagamento = _retido(db)
    fim = pagamentos.fim_do_servico(cenario.vaga)
    fixar_relogio(fim + _minutos("confirmacao_conclusao_min", db))
    pagamentos.concluir_automaticamente(db)
    db.add(Disputa(vaga_id=cenario.vaga.id, aberta_por_id=cenario.contratante.id, descricao="x"))
    db.commit()

    fixar_relogio(fim + _minutos("liberacao_parcial_min", db))
    assert pagamentos.liberar_parcialmente_sem_validacao(db) == 0
    db.refresh(pagamento)
    assert pagamento.estado == EstadoPagamento.RETIDO


def test_executar_varreduras_inclui_as_do_pagamento(db):
    resultado = varreduras.executar_varreduras(db)

    assert {"conclusao_automatica", "liberacao_parcial", "expiracao_pagamentos"} <= set(
        resultado["alteracoes"]
    )
