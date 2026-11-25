"""cancelamento, sancoes de conta e agenda de bloqueios

Revision ID: f6b9d3e5a772
Revises: e5a8c2d4f661
Create Date: 2026-11-27 14:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6b9d3e5a772"
down_revision: str | None = "e5a8c2d4f661"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("vagas") as lote:
        lote.add_column(sa.Column("cancelada_em", sa.DateTime(timezone=True), nullable=True))
        lote.add_column(sa.Column("cancelada_por_id", sa.Integer(), nullable=True))
        lote.add_column(sa.Column("motivo_cancelamento", sa.String(255), nullable=True))
        lote.create_foreign_key("fk_vagas_cancelada_por", "usuarios", ["cancelada_por_id"], ["id"])
    op.create_table(
        "bloqueios_agenda",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profissional_id",
            sa.Integer(),
            sa.ForeignKey("perfis_profissionais.id"),
            nullable=False,
        ),
        sa.Column("inicio_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fim_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("motivo", sa.String(120), nullable=True),
        sa.CheckConstraint("fim_em > inicio_em", name="ck_bloqueios_agenda_periodo"),
    )
    op.create_index("ix_bloqueios_agenda_profissional_id", "bloqueios_agenda", ["profissional_id"])
    op.create_table(
        "sancoes_conta",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("tipo", sa.String(12), nullable=False),
        sa.Column("origem", sa.String(20), nullable=False),
        sa.Column("motivo", sa.String(255), nullable=False),
        sa.Column("inicio_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fim_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ativa", sa.Boolean(), nullable=False),
        sa.Column("encerrada_em", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sancoes_conta_usuario_id", "sancoes_conta", ["usuario_id"])
    op.create_index("ix_sancoes_conta_ativa", "sancoes_conta", ["ativa"])
    op.create_table(
        "contestacoes_conta",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "sancao_id",
            sa.Integer(),
            sa.ForeignKey("sancoes_conta.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("usuario_id", sa.Integer(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("texto", sa.String(1000), nullable=False),
        sa.Column("criada_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decidida_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("aceita", sa.Boolean(), nullable=True),
        sa.Column("motivo_decisao", sa.String(255), nullable=True),
    )
    op.create_index("ix_contestacoes_conta_usuario_id", "contestacoes_conta", ["usuario_id"])


def downgrade() -> None:
    op.drop_table("contestacoes_conta")
    op.drop_table("sancoes_conta")
    op.drop_table("bloqueios_agenda")
    with op.batch_alter_table("vagas") as lote:
        lote.drop_constraint("fk_vagas_cancelada_por", type_="foreignkey")
        lote.drop_column("motivo_cancelamento")
        lote.drop_column("cancelada_por_id")
        lote.drop_column("cancelada_em")
