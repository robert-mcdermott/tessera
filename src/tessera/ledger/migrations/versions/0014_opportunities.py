"""Opportunities an organization tracks itself (INT-12).

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-08 20:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import tessera.ledger.types

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DT = tessera.ledger.types.UtcDateTime(timezone=True)
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "opportunity",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("source", sa.String(length=200), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=True),
        sa.Column("kind", sa.String(length=20), server_default="foundation", nullable=False),
        sa.Column("number", sa.String(length=100), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("sponsor", sa.String(length=300), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("url", sa.String(length=1000), nullable=True),
        sa.Column("posted_on", sa.Date(), nullable=True),
        sa.Column("closes_on", sa.Date(), nullable=True),
        sa.Column("internal_deadline", sa.Date(), nullable=True),
        sa.Column("award_floor", sa.BigInteger(), nullable=True),
        sa.Column("award_ceiling", sa.BigInteger(), nullable=True),
        sa.Column("limited_submission", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("eligibility_notes", sa.Text(), nullable=True),
        sa.Column("applicant_types", JSON, nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("created_at", DT, nullable=False),
        sa.Column("updated_by", sa.String(length=200), nullable=False),
        sa.Column("updated_at", DT, nullable=False),
        sa.Column("archived_at", DT, nullable=True),
        sa.CheckConstraint(
            "kind IN ('foundation', 'internal', 'federal', 'state', 'industry', 'other')",
            name=op.f("ck_opportunity_kind"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_opportunity")),
        sa.UniqueConstraint(
            "source", "external_id", name=op.f("uq_opportunity_source_external_id")
        ),
    )
    with op.batch_alter_table("opportunity", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_opportunity_closes_on"), ["closes_on"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("opportunity", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_opportunity_closes_on"))
    op.drop_table("opportunity")
