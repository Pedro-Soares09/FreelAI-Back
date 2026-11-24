"""plano pro e exclusao de conta

Revision ID: b2d5f8a1c330
Revises: a1c4e7b9d210
Create Date: 2026-11-25 12:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2d5f8a1c330"
down_revision: str | None = "a1c4e7b9d210"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("usuarios") as lote:
        lote.add_column(sa.Column("excluida_em", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "assinaturas_pro",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profissional_id",
            sa.Integer(),
            sa.ForeignKey("perfis_profissionais.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("valor_mensal", sa.Numeric(8, 2), nullable=False),
        sa.Column("iniciada_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valido_ate", sa.DateTime(timezone=True), nullable=False),
        sa.Column("renovacao_automatica", sa.Boolean(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("assinaturas_pro")
    with op.batch_alter_table("usuarios") as lote:
        lote.drop_column("excluida_em")
