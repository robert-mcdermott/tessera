"""The optional assistant's log (wave 5).

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-09 02:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import tessera.ledger.types

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DT = tessera.ledger.types.UtcDateTime(timezone=True)
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "ai_exchange",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("owner", sa.String(length=200), nullable=False),
        sa.Column("asked_by", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("request", JSON, nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("citations", JSON, nullable=False),
        sa.Column("usage", JSON, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", DT, nullable=False),
        sa.Column("seconds", sa.Float(), nullable=True),
        sa.CheckConstraint(
            "status IN ('answered', 'declined', 'failed', 'limited')",
            name=op.f("ck_ai_exchange_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_exchange")),
    )
    with op.batch_alter_table("ai_exchange", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ai_exchange_owner_started_at"), ["owner", "started_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_ai_exchange_started_at"), ["started_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("ai_exchange", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ai_exchange_started_at"))
        batch_op.drop_index(batch_op.f("ix_ai_exchange_owner_started_at"))
    op.drop_table("ai_exchange")
