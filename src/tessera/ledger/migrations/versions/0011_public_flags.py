"""Opting in to the public site: people and groups each choose whether ``tessera publish`` gives
them a public page (PLT-07).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-09 05:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("person", "unit"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("public", sa.Boolean(), server_default=sa.false(), nullable=False)
            )


def downgrade() -> None:
    for table in ("unit", "person"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column("public")
