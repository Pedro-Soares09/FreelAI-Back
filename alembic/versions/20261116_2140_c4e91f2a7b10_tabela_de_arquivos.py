"""tabela de arquivos

Revision ID: c4e91f2a7b10
Revises: 88fd93bd4236
Create Date: 2026-11-16 21:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e91f2a7b10"
down_revision: str | None = "88fd93bd4236"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "arquivos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=False),
        sa.Column(
            "tipo",
            sa.Enum(
                "foto",
                "selfie",
                "documento",
                "qualificacao",
                "epi",
                "anexo",
                name="tipoarquivo",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("chave", sa.String(length=120), nullable=False),
        sa.Column("mime_type", sa.String(length=64), nullable=False),
        sa.Column("tamanho_bytes", sa.Integer(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("arquivos", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_arquivos_chave"), ["chave"], unique=True)
        batch_op.create_index(batch_op.f("ix_arquivos_usuario_id"), ["usuario_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("arquivos", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_arquivos_usuario_id"))
        batch_op.drop_index(batch_op.f("ix_arquivos_chave"))

    op.drop_table("arquivos")
