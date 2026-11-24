"""comprovantes de pagamento

Revision ID: c3e6a9b2d440
Revises: b2d5f8a1c330
Create Date: 2026-11-26 09:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3e6a9b2d440"
down_revision: str | None = "b2d5f8a1c330"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "comprovantes_pagamento",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("vaga_id", sa.Integer(), sa.ForeignKey("vagas.id"), nullable=False),
        sa.Column(
            "tipo",
            sa.Enum(
                "servico",
                "horas_extras",
                "bonus",
                name="tipocomprovante",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("valor", sa.Numeric(10, 2), nullable=False),
        sa.Column("arquivo_id", sa.Integer(), sa.ForeignKey("arquivos.id"), nullable=False),
        sa.Column("enviado_por_id", sa.Integer(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("acordo_previo", sa.Text(), nullable=True),
        sa.Column("taxa_pct", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("taxa_valor", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("valor > 0", name="ck_comprovantes_valor"),
    )
    op.create_index("ix_comprovantes_pagamento_vaga_id", "comprovantes_pagamento", ["vaga_id"])
    op.create_index(
        "ix_comprovantes_pagamento_enviado_por_id", "comprovantes_pagamento", ["enviado_por_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_comprovantes_pagamento_enviado_por_id", "comprovantes_pagamento")
    op.drop_index("ix_comprovantes_pagamento_vaga_id", "comprovantes_pagamento")
    op.drop_table("comprovantes_pagamento")
