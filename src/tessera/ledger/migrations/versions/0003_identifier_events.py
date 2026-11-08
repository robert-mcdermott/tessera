"""Identifier decisions in person_event: an "identifier" action and a detail column.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-08 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ACTIONS = "action IN ('promote', 'dismiss', 'undo')"
NEW_ACTIONS = "action IN ('promote', 'dismiss', 'undo', 'identifier')"


def upgrade() -> None:
    with op.batch_alter_table("person_event", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "detail",
                sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
                nullable=True,
            )
        )
        batch_op.drop_constraint(batch_op.f("ck_person_event_action"), type_="check")
        batch_op.create_check_constraint(batch_op.f("ck_person_event_action"), sa.text(NEW_ACTIONS))


def downgrade() -> None:
    # The old schema has no place for identifier decisions; the identifiers themselves stay.
    op.execute("DELETE FROM person_event WHERE action = 'identifier'")
    with op.batch_alter_table("person_event", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_person_event_action"), type_="check")
        batch_op.create_check_constraint(batch_op.f("ck_person_event_action"), sa.text(OLD_ACTIONS))
        batch_op.drop_column("detail")
