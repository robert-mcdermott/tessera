"""Each person's public page keeps its address (wave 8): the address is chosen from their name
the first time the page is published, and kept, so that links to it keep working even when
someone of the same name opts in later. People public now keep the address the site gives them
today, which is filled in at the next ``tessera publish``.

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-09 19:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("person", schema=None) as batch_op:
        batch_op.add_column(sa.Column("public_slug", sa.String(length=200), nullable=True))
        batch_op.create_index(batch_op.f("ix_person_public_slug"), ["public_slug"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("person", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_person_public_slug"))
        batch_op.drop_column("public_slug")
