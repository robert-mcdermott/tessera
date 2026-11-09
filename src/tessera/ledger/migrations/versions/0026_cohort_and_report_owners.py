"""Saved cohorts and reports belong to an account, not a display name (wave 8): names are not
unique, and single sign-on rewrites them at each sign-in. ``owner`` is the account's ID, or
"operator" in single-operator mode; those saved before are given the account whose display name
they recorded, when exactly one account has it, and are otherwise left to admins.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-09 18:00:00
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("cohort", "report")


def upgrade() -> None:
    for name in TABLES:
        with op.batch_alter_table(name, schema=None) as batch_op:
            batch_op.add_column(sa.Column("owner", sa.String(length=40), nullable=True))
    conn = op.get_bind()
    account = sa.table("account", sa.column("id", sa.String), sa.column("display_name", sa.String))
    accounts = conn.execute(sa.select(account.c.id, account.c.display_name)).all()
    named = Counter(name for _, name in accounts)
    unique = {name: aid for aid, name in accounts if named[name] == 1}
    for name in TABLES:
        table = sa.table(name, sa.column("id", sa.String), sa.column("created_by", sa.String),
                         sa.column("owner", sa.String))  # fmt: skip
        for row_id, created_by in conn.execute(sa.select(table.c.id, table.c.created_by)).all():
            if created_by in unique:
                conn.execute(
                    sa.update(table).where(table.c.id == row_id).values(owner=unique[created_by])
                )


def downgrade() -> None:
    for name in TABLES:
        with op.batch_alter_table(name, schema=None) as batch_op:
            batch_op.drop_column("owner")
