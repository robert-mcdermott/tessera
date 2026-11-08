"""Auto-accept batches: which links one automatic run accepted, so it can be undone together.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-08 18:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import tessera.ledger.types

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DT = tessera.ledger.types.UtcDateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "auto_accept_batch",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("created_at", DT, nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("precision", sa.Float(), nullable=True),
        sa.Column("method", sa.String(length=60), nullable=False),
        sa.Column(
            "assertion_ids",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("undone_at", DT, nullable=True),
        sa.Column("undone_by", sa.String(length=200), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auto_accept_batch")),
    )


def downgrade() -> None:
    op.drop_table("auto_accept_batch")
