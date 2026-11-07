"""Person curation: a "dismissed" status for discovered candidates, and person_event.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-07 22:10:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import tessera.ledger.types

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_STATUSES = "status IN ('active', 'candidate', 'former')"
NEW_STATUSES = "status IN ('active', 'candidate', 'former', 'dismissed')"


def upgrade() -> None:
    with op.batch_alter_table("person", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_person_status"), type_="check")
        batch_op.create_check_constraint(batch_op.f("ck_person_status"), sa.text(NEW_STATUSES))
    op.create_table(
        "person_event",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("person_id", sa.String(length=40), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("from_status", sa.String(length=16), nullable=True),
        sa.Column("to_status", sa.String(length=16), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("at", tessera.ledger.types.UtcDateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('promote', 'dismiss', 'undo')", name=op.f("ck_person_event_action")
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["person.id"],
            name=op.f("fk_person_event_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_person_event")),
    )
    with op.batch_alter_table("person_event", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_person_event_person_id"), ["person_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_person_event_at"), ["at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("person_event", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_person_event_at"))
        batch_op.drop_index(batch_op.f("ix_person_event_person_id"))
    op.drop_table("person_event")
    # Dismissed people become candidates again; the old schema has no other place for them.
    op.execute("UPDATE person SET status = 'candidate' WHERE status = 'dismissed'")
    with op.batch_alter_table("person", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_person_status"), type_="check")
        batch_op.create_check_constraint(batch_op.f("ck_person_status"), sa.text(OLD_STATUSES))
