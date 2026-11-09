"""Accounts a person names to keep their profile for them (wave 6).

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-09 08:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import tessera.ledger.types

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DT = tessera.ledger.types.UtcDateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "profile_delegate",
        sa.Column("person_id", sa.String(length=40), nullable=False),
        sa.Column("account_id", sa.String(length=40), nullable=False),
        sa.Column("granted_by", sa.String(length=200), nullable=False),
        sa.Column("granted_at", DT, nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["account.id"],
            name=op.f("fk_profile_delegate_account_id_account"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["person.id"],
            name=op.f("fk_profile_delegate_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("person_id", "account_id", name=op.f("pk_profile_delegate")),
    )
    with op.batch_alter_table("profile_delegate", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_profile_delegate_account_id"), ["account_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("profile_delegate", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_profile_delegate_account_id"))
    op.drop_table("profile_delegate")
