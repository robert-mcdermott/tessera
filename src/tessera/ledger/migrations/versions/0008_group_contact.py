"""Group contact details: an email address and a website for each group's page.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-09 00:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("unit", schema=None) as batch_op:
        batch_op.add_column(sa.Column("email", sa.String(length=320), nullable=True))
        batch_op.add_column(sa.Column("website", sa.String(length=500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("unit", schema=None) as batch_op:
        batch_op.drop_column("website")
        batch_op.drop_column("email")
