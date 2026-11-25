"""personalizacao do perfil do contratante

Revision ID: d4f7b1c3e550
Revises: c3e6a9b2d440
Create Date: 2026-11-27 09:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4f7b1c3e550"
down_revision: str | None = "c3e6a9b2d440"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("perfis_contratantes") as lote:
        lote.add_column(sa.Column("bio", sa.String(300), nullable=True))
        lote.add_column(sa.Column("bairro", sa.String(80), nullable=True))
        lote.add_column(sa.Column("horario_contato", sa.String(80), nullable=True))
        lote.add_column(sa.Column("categorias_interesse", sa.JSON(), nullable=True))
        lote.add_column(
            sa.Column("exigir_verificado", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        lote.add_column(
            sa.Column("aceita_notificacoes", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        lote.add_column(
            sa.Column("cor_destaque", sa.String(20), nullable=False, server_default="lilas")
        )
        lote.add_column(sa.Column("avatar_url", sa.String(255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("perfis_contratantes") as lote:
        for coluna in (
            "avatar_url",
            "cor_destaque",
            "aceita_notificacoes",
            "exigir_verificado",
            "categorias_interesse",
            "horario_contato",
            "bairro",
            "bio",
        ):
            lote.drop_column(coluna)
