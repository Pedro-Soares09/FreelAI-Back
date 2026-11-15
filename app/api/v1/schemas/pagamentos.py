"""Schemas do pagamento Pix: cobrança, divisão de valores e livro de lançamentos.

Valores monetários são `Decimal` e chegam ao front como texto com duas casas ("150.00").
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import EstadoPagamento, EstadoVaga, TipoLancamento

EXEMPLO_LANCAMENTOS = [
    {
        "id": 1,
        "tipo": "retencao",
        "valor": "150.00",
        "descricao": "Pix recebido e retido até a conclusão do serviço",
        "criado_em": "2026-11-20T13:50:00Z",
    },
    {
        "id": 2,
        "tipo": "liberacao_profissional",
        "valor": "135.00",
        "descricao": "Liberação integral",
        "criado_em": "2026-11-20T18:10:00Z",
    },
    {
        "id": 3,
        "tipo": "taxa_plataforma",
        "valor": "15.00",
        "descricao": "Taxa da plataforma",
        "criado_em": "2026-11-20T18:10:00Z",
    },
]


class LancamentoResumo(BaseModel):
    id: int
    tipo: TipoLancamento
    valor: Decimal
    descricao: str | None = None
    criado_em: datetime

    model_config = ConfigDict(
        from_attributes=True, json_schema_extra={"examples": [EXEMPLO_LANCAMENTOS[0]]}
    )


class DivisaoValores(BaseModel):
    """Como o valor total se reparte entre profissional e plataforma."""

    valor_total: Decimal
    percentual_taxa: Decimal = Field(description="Taxa da plataforma, de 0 a 100.")
    taxa_plataforma: Decimal
    valor_liquido_profissional: Decimal

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "valor_total": "150.00",
                    "percentual_taxa": "10",
                    "taxa_plataforma": "15.00",
                    "valor_liquido_profissional": "135.00",
                }
            ]
        }
    )


class PagamentoDetalhe(BaseModel):
    """Estado da cobrança Pix de uma vaga, com a divisão prevista e o livro de lançamentos."""

    id: int
    vaga_id: int
    vaga_estado: EstadoVaga
    estado: EstadoPagamento
    metodo: Literal["pix"] = "pix"
    codigo_pix: str | None = Field(description="Código copia e cola exibido ao contratante.")
    expira_em: datetime | None = Field(description="Limite para confirmar o Pix.")
    divisao: DivisaoValores
    saldo_retido: Decimal = Field(description="Valor ainda retido; zero depois do estado final.")
    lancamentos: list[LancamentoResumo]
    criado_em: datetime
    atualizado_em: datetime

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": 5,
                    "vaga_id": 42,
                    "vaga_estado": "liberada",
                    "estado": "liberado",
                    "metodo": "pix",
                    "codigo_pix": "PIX-SIM-000005-1A2B3C4D5E6F7A8B9C0D",
                    "expira_em": "2026-11-20T14:05:00Z",
                    "divisao": DivisaoValores.model_config["json_schema_extra"]["examples"][0],
                    "saldo_retido": "0.00",
                    "lancamentos": EXEMPLO_LANCAMENTOS,
                    "criado_em": "2026-11-20T13:50:00Z",
                    "atualizado_em": "2026-11-20T18:10:00Z",
                }
            ]
        }
    )


class ValidacaoFacialEntrada(BaseModel):
    selfie_url: str | None = Field(
        default=None,
        max_length=255,
        description="Imagem enviada para o reconhecimento; opcional na simulação.",
        examples=["https://arquivos.exemplo/selfies/42.jpg"],
    )
