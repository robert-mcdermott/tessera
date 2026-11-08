"""Reversible person merges: a "merged" status, person.merged_into, and person_merge.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-08 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import tessera.ledger.types

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_STATUSES = "status IN ('active', 'candidate', 'former', 'dismissed')"
NEW_STATUSES = "status IN ('active', 'candidate', 'former', 'dismissed', 'merged')"
OLD_ACTIONS = "action IN ('promote', 'dismiss', 'undo', 'identifier')"
NEW_ACTIONS = "action IN ('promote', 'dismiss', 'undo', 'identifier', 'merge', 'unmerge')"
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("person", schema=None) as batch_op:
        batch_op.add_column(sa.Column("merged_into", sa.String(length=40), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_person_merged_into_person"),
            "person",
            ["merged_into"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.drop_constraint(batch_op.f("ck_person_status"), type_="check")
        batch_op.create_check_constraint(batch_op.f("ck_person_status"), sa.text(NEW_STATUSES))
    with op.batch_alter_table("person_event", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_person_event_action"), type_="check")
        batch_op.create_check_constraint(batch_op.f("ck_person_event_action"), sa.text(NEW_ACTIONS))
    op.create_table(
        "person_merge",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("merged_id", sa.String(length=40), nullable=False),
        sa.Column("survivor_id", sa.String(length=40), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("at", tessera.ledger.types.UtcDateTime(timezone=True), nullable=False),
        sa.Column("journal", JSON, nullable=False),
        sa.Column("summary", JSON, nullable=False),
        sa.Column("undone_at", tessera.ledger.types.UtcDateTime(timezone=True), nullable=True),
        sa.Column("undone_by", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(
            ["merged_id"],
            ["person.id"],
            name=op.f("fk_person_merge_merged_id_person"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["survivor_id"],
            ["person.id"],
            name=op.f("fk_person_merge_survivor_id_person"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_person_merge")),
    )
    with op.batch_alter_table("person_merge", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_person_merge_merged_id"), ["merged_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_person_merge_survivor_id"), ["survivor_id"], unique=False
        )


def downgrade() -> None:
    bind = op.get_bind()
    active = bind.execute(sa.text("SELECT count(*) FROM person_merge WHERE undone_at IS NULL"))
    if active.scalar():
        raise RuntimeError(
            "This ledger has merged people. Unmerge them before downgrading below revision 0004."
        )
    with op.batch_alter_table("person_merge", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_person_merge_survivor_id"))
        batch_op.drop_index(batch_op.f("ix_person_merge_merged_id"))
    op.drop_table("person_merge")
    op.execute("DELETE FROM person_event WHERE action IN ('merge', 'unmerge')")
    with op.batch_alter_table("person_event", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_person_event_action"), type_="check")
        batch_op.create_check_constraint(batch_op.f("ck_person_event_action"), sa.text(OLD_ACTIONS))
    with op.batch_alter_table("person", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_person_status"), type_="check")
        batch_op.create_check_constraint(batch_op.f("ck_person_status"), sa.text(OLD_STATUSES))
        batch_op.drop_constraint(batch_op.f("fk_person_merged_into_person"), type_="foreignkey")
        batch_op.drop_column("merged_into")
