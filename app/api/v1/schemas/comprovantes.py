"""Esquemas dos comprovantes de pagamento (serviço, horas extras e bônus)."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.models import TipoComprovante


class ComprovanteDetalhe(BaseModel):
    id: int
    vaga_id: int
    tipo: TipoComprovante
    valor: Decimal
    taxa_pct: Decimal = Field(description="Percentual da plataforma sobre o valor adicional.")
    taxa_plataforma: Decimal = Field(description="Taxa da plataforma sobre este comprovante.")
    acordo_previo: str | None = None
    enviado_por_id: int
    arquivo_url: str = Field(description="Rota que serve o arquivo, restrita às partes e à admin.")
    criado_em: datetime


class ResumoComprovantes(BaseModel):
    vaga_id: int
    comprovantes: list[ComprovanteDetalhe]
    total_servico: Decimal
    total_horas_extras: Decimal
    total_bonus: Decimal
    total_adicional: Decimal = Field(description="Horas extras mais bônus.")
    percentual_taxa_adicional: Decimal
    taxa_adicional_plataforma: Decimal
    liquido_adicional_profissional: Decimal


class DestaqueContratante(BaseModel):
    adicionais_pagos: int = 0
    total_adicional: Decimal = Decimal("0.00")
    destaque: bool = False
