"""restricoes e indices da revisao

Revision ID: 88fd93bd4236
Revises: 9bac6fa60cac
Create Date: 2026-11-15 18:08:14.240637
"""

from collections.abc import Sequence

from alembic import op

revision: str = "88fd93bd4236"
down_revision: str | None = "9bac6fa60cac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("categorias_profissional") as lote:
        lote.create_index("ix_categorias_profissional_categoria_id", ["categoria_id"])

    with op.batch_alter_table("convites") as lote:
        lote.create_index("ix_convites_profissional_id", ["profissional_id"])

    with op.batch_alter_table("experiencias") as lote:
        lote.create_index("ix_experiencias_profissional_id", ["profissional_id"])

    with op.batch_alter_table("vagas") as lote:
        lote.create_index("ix_vagas_profissional_id", ["profissional_id"])
        lote.create_check_constraint("ck_vagas_duracao", "duracao_minutos > 0")

    with op.batch_alter_table("lancamentos") as lote:
        lote.create_unique_constraint("uq_lancamentos_pagamento_tipo", ["pagamento_id", "tipo"])

    with op.batch_alter_table("perfis_profissionais") as lote:
        lote.create_check_constraint("ck_perfis_profissionais_raio", "raio_km > 0")

    with op.batch_alter_table("avaliacoes") as lote:
        lote.create_check_constraint("ck_avaliacoes_nota", "nota BETWEEN 1 AND 5")

    with op.batch_alter_table("disputas") as lote:
        lote.create_check_constraint(
            "ck_disputas_percentual",
            "percentual_reembolso IS NULL OR percentual_reembolso BETWEEN 0 AND 100",
        )

    with op.batch_alter_table("mensagens") as lote:
        lote.create_check_constraint(
            "ck_mensagens_conteudo", "texto IS NOT NULL OR anexo_url IS NOT NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("mensagens") as lote:
        lote.drop_constraint("ck_mensagens_conteudo", type_="check")

    with op.batch_alter_table("disputas") as lote:
        lote.drop_constraint("ck_disputas_percentual", type_="check")

    with op.batch_alter_table("avaliacoes") as lote:
        lote.drop_constraint("ck_avaliacoes_nota", type_="check")

    with op.batch_alter_table("perfis_profissionais") as lote:
        lote.drop_constraint("ck_perfis_profissionais_raio", type_="check")

    with op.batch_alter_table("lancamentos") as lote:
        lote.drop_constraint("uq_lancamentos_pagamento_tipo", type_="unique")

    with op.batch_alter_table("vagas") as lote:
        lote.drop_constraint("ck_vagas_duracao", type_="check")
        lote.drop_index("ix_vagas_profissional_id")

    with op.batch_alter_table("experiencias") as lote:
        lote.drop_index("ix_experiencias_profissional_id")

    with op.batch_alter_table("convites") as lote:
        lote.drop_index("ix_convites_profissional_id")

    with op.batch_alter_table("categorias_profissional") as lote:
        lote.drop_index("ix_categorias_profissional_categoria_id")
