"""tabela tentativas_contato

Revision ID: d7a3e8f1c204
Revises: 88fd93bd4236
Create Date: 2026-11-23 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7a3e8f1c204"
down_revision: str | None = "88fd93bd4236"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tentativas_contato",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("conversa_id", sa.Integer(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=False),
        sa.Column("conteudo", sa.Text(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversa_id"], ["conversas.id"]),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("tentativas_contato", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_tentativas_contato_conversa_id"), ["conversa_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_tentativas_contato_usuario_id"), ["usuario_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("tentativas_contato", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_tentativas_contato_usuario_id"))
        batch_op.drop_index(batch_op.f("ix_tentativas_contato_conversa_id"))
    op.drop_table("tentativas_contato")
