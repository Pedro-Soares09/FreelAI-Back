"""Cobrança Pix, retenção, liberação e estorno com livro de lançamentos.

O pagamento de uma vaga é feito somente por Pix. O valor fica retido até a conclusão do serviço
e sai da retenção apenas por lançamentos novos (liberação ao profissional, taxa da plataforma e
estorno ao contratante). Lançamentos nunca são alterados nem removidos, então a soma das saídas
de um pagamento encerrado é sempre igual ao valor retido.

O gateway Pix e o reconhecimento facial ficam atrás de interfaces trocáveis; nesta fase ambos
são simulados.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Protocol

from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.erros import erro_http
from app.models import (
    Disputa,
    EstadoDisputa,
    EstadoPagamento,
    EstadoVaga,
    Lancamento,
    Pagamento,
    PerfilContratante,
    TipoLancamento,
    Usuario,
    Vaga,
)
from app.services import biometria, configuracao
from app.services.varreduras import varredura
from app.tempo import agora, como_utc

CENTAVOS = Decimal("0.01")
CEM = Decimal("100")

SituacaoCobranca = Literal["aguardando", "paga", "expirada", "estornada"]


@dataclass(frozen=True)
class Cobranca:
    codigo_pix: str
    valor: Decimal
    expira_em: datetime


class GatewayPagamento(Protocol):
    def criar_cobranca(self, pagamento_id: int, valor: Decimal, expira_em: datetime) -> Cobranca:
        """Registra a cobrança no provedor e devolve o código Pix a ser exibido."""
        ...

    def consultar(self, codigo_pix: str) -> SituacaoCobranca: ...

    def estornar(self, codigo_pix: str, valor: Decimal) -> str:
        """Devolve o valor ao pagador e retorna o identificador do estorno no provedor."""
        ...


class GatewaySimulado:
    """Provedor Pix fictício: códigos determinísticos e sem comunicação externa.

    A confirmação do pagamento não vem do provedor; ela é simulada pela rota própria do
    perfil de demonstração.
    """

    PREFIXO = "PIX-SIM"

    @staticmethod
    def _assinatura(semente: str) -> str:
        return hashlib.sha256(semente.encode()).hexdigest()[:20].upper()

    def criar_cobranca(self, pagamento_id: int, valor: Decimal, expira_em: datetime) -> Cobranca:
        codigo = f"{self.PREFIXO}-{pagamento_id:06d}-{self._assinatura(f'cobranca:{pagamento_id}')}"
        return Cobranca(codigo_pix=codigo, valor=valor, expira_em=expira_em)

    def consultar(self, codigo_pix: str) -> SituacaoCobranca:
        return "aguardando"

    def estornar(self, codigo_pix: str, valor: Decimal) -> str:
        return f"EST-SIM-{self._assinatura(f'estorno:{codigo_pix}:{valor}')}"


def obter_gateway() -> GatewayPagamento:
    return GatewaySimulado()


# ---------------------------------------------------------------------------
# Cálculo
# ---------------------------------------------------------------------------


def _para_decimal(valor: Decimal | int | float | str) -> Decimal:
    return valor if isinstance(valor, Decimal) else Decimal(str(valor))


def _centavos(valor: Decimal) -> Decimal:
    return valor.quantize(CENTAVOS, rounding=ROUND_HALF_UP)


def taxa_configurada(db: Session | None = None) -> Decimal:
    """Percentual da taxa da plataforma em vigor (de 0 a 100)."""
    return _para_decimal(configuracao.get("taxa_plataforma_pct", db=db))


def calcular_divisao(
    valor_total: Decimal,
    percentual_taxa: Decimal | int | None = None,
    *,
    db: Session | None = None,
) -> tuple[Decimal, Decimal]:
    """Divide `valor_total` em (líquido do profissional, taxa da plataforma).

    A taxa é arredondada a centavos (meio para cima) e o líquido é a diferença, de modo que
    líquido + taxa == valor_total sem resíduo.
    """
    total = _centavos(_para_decimal(valor_total))
    pct = taxa_configurada(db) if percentual_taxa is None else _para_decimal(percentual_taxa)
    taxa = _centavos(total * pct / CEM)
    return total - taxa, taxa


def saldo_retido(pagamento: Pagamento) -> Decimal:
    """Valor ainda retido: entradas de retenção menos todas as saídas registradas."""
    saldo = Decimal("0.00")
    for lancamento in pagamento.lancamentos:
        valor = _para_decimal(lancamento.valor)
        if lancamento.tipo == TipoLancamento.RETENCAO:
            saldo += valor
        else:
            saldo -= valor
    return _centavos(saldo)


# ---------------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------------


def fim_do_servico(vaga: Vaga) -> datetime:
    return como_utc(vaga.inicio_em) + timedelta(minutes=vaga.duracao_minutos)


def pagamento_da_vaga(db: Session, vaga: Vaga) -> Pagamento | None:
    return db.scalar(select(Pagamento).where(Pagamento.vaga_id == vaga.id))


def _vaga_do_pagamento(db: Session, pagamento: Pagamento) -> Vaga:
    vaga = db.get(Vaga, pagamento.vaga_id)
    if vaga is None:
        raise erro_http(
            status.HTTP_404_NOT_FOUND, "vaga_nao_encontrada", "A vaga do pagamento não existe."
        )
    return vaga


def _usuario_do_contratante(db: Session, vaga: Vaga) -> int | None:
    return db.scalar(
        select(PerfilContratante.usuario_id).where(PerfilContratante.id == vaga.contratante_id)
    )


def exigir_contratante_dono(db: Session, vaga: Vaga, usuario_id: int) -> None:
    if _usuario_do_contratante(db, vaga) != usuario_id:
        raise erro_http(
            status.HTTP_403_FORBIDDEN,
            "vaga_de_outro_contratante",
            "Somente o contratante que publicou a vaga pode fazer esta ação.",
        )


def _erro_estado_pagamento(pagamento: Pagamento, esperado: EstadoPagamento):
    return erro_http(
        status.HTTP_409_CONFLICT,
        "pagamento_estado_invalido",
        f"O pagamento está {pagamento.estado.value}; a ação exige {esperado.value}.",
        estado_atual=pagamento.estado.value,
    )


def _lancar(
    db: Session, pagamento: Pagamento, tipo: TipoLancamento, valor: Decimal, descricao: str
) -> None:
    # Lançamentos de valor zero não acrescentam informação ao livro e são omitidos.
    if valor <= 0:
        return
    db.add(Lancamento(pagamento_id=pagamento.id, tipo=tipo, valor=valor, descricao=descricao))


def _exigir_retido(pagamento: Pagamento) -> None:
    if pagamento.estado != EstadoPagamento.RETIDO:
        raise _erro_estado_pagamento(pagamento, EstadoPagamento.RETIDO)


# ---------------------------------------------------------------------------
# Ciclo do pagamento
# ---------------------------------------------------------------------------


def criar_pagamento(db: Session, vaga: Vaga) -> Pagamento:
    """Abre a cobrança Pix da vaga e a coloca em espera pelo pagamento.

    Uma cobrança expirada da mesma vaga é reaproveitada: ela nunca reteve valor, então basta
    emitir um novo código e um novo prazo.
    """
    if vaga.estado not in (EstadoVaga.ACEITA, EstadoVaga.AGUARDANDO_PAGAMENTO):
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "vaga_nao_aceita",
            "A cobrança só pode ser aberta depois que o profissional aceita a vaga.",
            estado_atual=vaga.estado.value,
        )
    existente = pagamento_da_vaga(db, vaga)
    if existente is not None and existente.estado != EstadoPagamento.EXPIRADO:
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "pagamento_ja_existe",
            "Esta vaga já possui uma cobrança.",
            pagamento_id=existente.id,
            estado_atual=existente.estado.value,
        )

    valor = _centavos(_para_decimal(vaga.valor_fixado or vaga.valor_inicial))
    prazo = timedelta(minutes=configuracao.get("prazo_pagamento_min", db=db))
    pagamento = existente or Pagamento(vaga_id=vaga.id, valor_total=valor)
    pagamento.valor_total = valor
    pagamento.estado = EstadoPagamento.AGUARDANDO
    pagamento.expira_em = agora() + prazo
    db.add(pagamento)
    db.flush()

    cobranca = obter_gateway().criar_cobranca(pagamento.id, valor, pagamento.expira_em)
    pagamento.codigo_pix = cobranca.codigo_pix
    vaga.estado = EstadoVaga.AGUARDANDO_PAGAMENTO
    db.commit()
    db.refresh(pagamento)
    return pagamento


def reter(db: Session, pagamento: Pagamento) -> Pagamento:
    """Confirma a entrada do Pix: o valor passa a retido e a vaga fica contratada."""
    if pagamento.estado == EstadoPagamento.RETIDO:
        return pagamento
    if pagamento.estado != EstadoPagamento.AGUARDANDO:
        raise _erro_estado_pagamento(pagamento, EstadoPagamento.AGUARDANDO)
    if pagamento.expira_em is not None and como_utc(pagamento.expira_em) <= agora():
        raise erro_http(
            status.HTTP_409_CONFLICT,
            "pagamento_expirado",
            "O prazo para pagar esta cobrança terminou.",
            expirou_em=como_utc(pagamento.expira_em).isoformat(),
        )

    vaga = _vaga_do_pagamento(db, pagamento)
    _lancar(
        db,
        pagamento,
        TipoLancamento.RETENCAO,
        _para_decimal(pagamento.valor_total),
        "Pix recebido e retido até a conclusão do serviço",
    )
    pagamento.estado = EstadoPagamento.RETIDO
    vaga.estado = EstadoVaga.CONTRATADA
    db.commit()
    db.refresh(pagamento)
    return pagamento


def liberar_integral(db: Session, pagamento: Pagamento) -> Pagamento:
    """Libera todo o valor retido ao profissional, descontada a taxa da plataforma."""
    if pagamento.estado == EstadoPagamento.LIBERADO:
        return pagamento
    _exigir_retido(pagamento)

    vaga = _vaga_do_pagamento(db, pagamento)
    liquido, taxa = calcular_divisao(_para_decimal(pagamento.valor_total), db=db)
    _lancar(db, pagamento, TipoLancamento.LIBERACAO_PROFISSIONAL, liquido, "Liberação integral")
    _lancar(db, pagamento, TipoLancamento.TAXA_PLATAFORMA, taxa, "Taxa da plataforma")
    pagamento.estado = EstadoPagamento.LIBERADO
    vaga.estado = EstadoVaga.LIBERADA
    db.commit()
    db.refresh(pagamento)
    return pagamento


def liberar_parcial(db: Session, pagamento: Pagamento, percentual: Decimal | int) -> Pagamento:
    """Libera `percentual` do valor ao profissional (menos a taxa) e estorna o restante."""
    pct = _para_decimal(percentual)
    if not 0 <= pct <= CEM:
        raise ValueError("O percentual da liberação parcial deve ficar entre 0 e 100.")
    if pagamento.estado == EstadoPagamento.LIBERADO_PARCIAL:
        return pagamento
    _exigir_retido(pagamento)

    vaga = _vaga_do_pagamento(db, pagamento)
    total = _para_decimal(pagamento.valor_total)
    parcela = _centavos(total * pct / CEM)
    estorno = total - parcela
    liquido, taxa = calcular_divisao(parcela, db=db)
    if estorno > 0:
        obter_gateway().estornar(pagamento.codigo_pix or "", estorno)
    _lancar(
        db,
        pagamento,
        TipoLancamento.LIBERACAO_PROFISSIONAL,
        liquido,
        f"Liberação parcial de {pct.normalize()}%",
    )
    _lancar(db, pagamento, TipoLancamento.TAXA_PLATAFORMA, taxa, "Taxa da plataforma")
    _lancar(db, pagamento, TipoLancamento.ESTORNO, estorno, "Estorno parcial ao contratante")
    pagamento.estado = EstadoPagamento.LIBERADO_PARCIAL
    vaga.estado = EstadoVaga.LIBERADA_PARCIAL
    db.commit()
    db.refresh(pagamento)
    return pagamento


def estornar_integral(
    db: Session, pagamento: Pagamento, *, estado_vaga: EstadoVaga = EstadoVaga.REEMBOLSADA
) -> Pagamento:
    """Devolve todo o valor retido ao contratante. Nenhuma taxa é cobrada."""
    if pagamento.estado == EstadoPagamento.ESTORNADO:
        return pagamento
    _exigir_retido(pagamento)

    vaga = _vaga_do_pagamento(db, pagamento)
    total = _para_decimal(pagamento.valor_total)
    obter_gateway().estornar(pagamento.codigo_pix or "", total)
    _lancar(db, pagamento, TipoLancamento.ESTORNO, total, "Estorno integral ao contratante")
    pagamento.estado = EstadoPagamento.ESTORNADO
    vaga.estado = estado_vaga
    db.commit()
    db.refresh(pagamento)
    return pagamento


