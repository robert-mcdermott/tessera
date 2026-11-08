"""Plain-language summaries and their history (wave 5).

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-09 04:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import tessera.ledger.types

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DT = tessera.ledger.types.UtcDateTime(timezone=True)
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "ai_summary",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("subject_type", sa.String(length=10), nullable=False),
        sa.Column("subject_id", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("draft", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("inputs", JSON, nullable=False),
        sa.Column("inputs_digest", sa.String(length=64), nullable=False),
        sa.Column("exchange_id", sa.String(length=40), nullable=True),
        sa.Column("requested_by", sa.String(length=200), nullable=False),
        sa.Column("requested_at", DT, nullable=False),
        sa.Column("decided_by", sa.String(length=200), nullable=True),
        sa.Column("decided_at", DT, nullable=True),
        sa.CheckConstraint(
            "subject_type IN ('person', 'group')", name=op.f("ck_ai_summary_subject_type")
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'rejected', 'replaced')",
            name=op.f("ck_ai_summary_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_summary")),
    )
    with op.batch_alter_table("ai_summary", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ai_summary_subject_type_subject_id_status"),
            ["subject_type", "subject_id", "status"],
            unique=False,
        )
    op.create_table(
        "summary_event",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("summary_id", sa.String(length=40), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("at", DT, nullable=False),
        sa.ForeignKeyConstraint(
            ["summary_id"],
            ["ai_summary.id"],
            name=op.f("fk_summary_event_summary_id_ai_summary"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_summary_event")),
    )
    with op.batch_alter_table("summary_event", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_summary_event_summary_id"), ["summary_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("summary_event", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_summary_event_summary_id"))
    op.drop_table("summary_event")
    with op.batch_alter_table("ai_summary", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ai_summary_subject_type_subject_id_status"))
    op.drop_table("ai_summary")
