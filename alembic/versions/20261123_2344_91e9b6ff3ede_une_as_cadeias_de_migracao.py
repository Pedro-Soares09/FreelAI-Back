"""une as cadeias de migracao

Revision ID: 91e9b6ff3ede
Revises: 6e5445ee0008, d7a3e8f1c204
Create Date: 2026-11-23 23:44:49.163753
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '91e9b6ff3ede'
down_revision: str | None = ('6e5445ee0008', 'd7a3e8f1c204')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
