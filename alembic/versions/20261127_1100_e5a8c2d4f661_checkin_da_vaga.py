"""checkin da vaga

Revision ID: e5a8c2d4f661
Revises: d4f7b1c3e550
Create Date: 2026-11-27 11:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5a8c2d4f661"
down_revision: str | None = "d4f7b1c3e550"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("vagas") as lote:
        lote.add_column(sa.Column("checkin_em", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("vagas") as lote:
        lote.drop_column("checkin_em")
