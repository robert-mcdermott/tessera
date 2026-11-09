"""Watching or dismissing an opportunity, for oneself (INT-09).

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-08 22:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import tessera.ledger.types

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DT = tessera.ledger.types.UtcDateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "opportunity_interest",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner", sa.String(length=200), nullable=False),
        sa.Column("opportunity", sa.String(length=80), nullable=False),
        sa.Column("state", sa.String(length=12), nullable=False),
        sa.Column("updated_at", DT, nullable=False),
        sa.CheckConstraint(
            "state IN ('watching', 'dismissed')", name=op.f("ck_opportunity_interest_state")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_opportunity_interest")),
        sa.UniqueConstraint(
            "owner", "opportunity", name=op.f("uq_opportunity_interest_owner_opportunity")
        ),
    )
    with op.batch_alter_table("opportunity_interest", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_opportunity_interest_owner"), ["owner"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("opportunity_interest", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_opportunity_interest_owner"))
    op.drop_table("opportunity_interest")
