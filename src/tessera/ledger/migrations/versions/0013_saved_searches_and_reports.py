"""Saved searches (DIS-10), reports, and report snapshots (INT-03).

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-08 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import tessera.ledger.types

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DT = tessera.ledger.types.UtcDateTime(timezone=True)
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "saved_search",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("owner", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("q", sa.Text(), server_default="", nullable=False),
        sa.Column("filters", JSON, nullable=False),
        sa.Column("seen", JSON, nullable=False),
        sa.Column("seen_at", DT, nullable=False),
        sa.Column("created_at", DT, nullable=False),
        sa.Column("updated_at", DT, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_saved_search")),
    )
    with op.batch_alter_table("saved_search", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_saved_search_owner"), ["owner"], unique=False)
    op.create_table(
        "report",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("cohort", sa.String(length=4000), nullable=False),
        sa.Column("start_year", sa.Integer(), nullable=True),
        sa.Column("end_year", sa.Integer(), nullable=True),
        sa.Column("figures", JSON, nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("created_at", DT, nullable=False),
        sa.Column("updated_by", sa.String(length=200), nullable=False),
        sa.Column("updated_at", DT, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report")),
        sa.UniqueConstraint("name", name=op.f("uq_report_name")),
    )
    op.create_table(
        "report_snapshot",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("report_id", sa.String(length=40), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("taken_by", sa.String(length=200), nullable=False),
        sa.Column("taken_at", DT, nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("path", sa.String(length=500), nullable=False),
        sa.Column("summary", JSON, nullable=False),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["report.id"],
            name=op.f("fk_report_snapshot_report_id_report"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_snapshot")),
    )
    with op.batch_alter_table("report_snapshot", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_report_snapshot_report_id"), ["report_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("report_snapshot", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_report_snapshot_report_id"))
    op.drop_table("report_snapshot")
    op.drop_table("report")
    with op.batch_alter_table("saved_search", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_saved_search_owner"))
    op.drop_table("saved_search")
