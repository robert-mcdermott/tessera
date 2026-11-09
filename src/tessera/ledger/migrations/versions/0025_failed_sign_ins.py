"""Failed sign-ins kept in the ledger, so the limit on guessing holds across server processes
and restarts (wave 8). Only a hash of each key is kept.

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-09 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import tessera.ledger.types

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DT = tessera.ledger.types.UtcDateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "failed_sign_in",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("failed_at", DT, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_failed_sign_in")),
    )
    with op.batch_alter_table("failed_sign_in", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_failed_sign_in_key_hash_failed_at"),
            ["key_hash", "failed_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("failed_sign_in", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_failed_sign_in_key_hash_failed_at"))
    op.drop_table("failed_sign_in")
