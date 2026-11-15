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


