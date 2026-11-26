"""envio da verificacao de identidade

Revision ID: a1c4e7b9d210
Revises: 91e9b6ff3ede
Create Date: 2026-11-25 10:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1c4e7b9d210"
down_revision: str | None = "91e9b6ff3ede"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("perfis_profissionais") as lote:
        lote.add_column(sa.Column("verificacao_enviada_em", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("perfis_contratantes") as lote:
        lote.add_column(sa.Column("verificacao_motivo", sa.String(length=255), nullable=True))
        lote.add_column(sa.Column("verificacao_enviada_em", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("perfis_contratantes") as lote:
        lote.drop_column("verificacao_enviada_em")
        lote.drop_column("verificacao_motivo")
    with op.batch_alter_table("perfis_profissionais") as lote:
        lote.drop_column("verificacao_enviada_em")
