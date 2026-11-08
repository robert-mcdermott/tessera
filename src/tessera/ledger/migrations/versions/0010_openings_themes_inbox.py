"""Openings, themes and projects, and the group inbox.

* ``opening``: a job or training opportunity a group posts, closing on its own date (LAB-08).
* ``theme`` and ``theme_link``: a group's research themes and projects, linking people,
  grants, publications, and studies (LAB-07).
* ``group_inbox``: when a group's editors last reviewed its weekly inbox (LAB-06).

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-09 04:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import tessera.ledger.types

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DT = tessera.ledger.types.UtcDateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "opening",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("unit_id", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("contact", sa.String(length=320), nullable=True),
        sa.Column("posted_on", sa.Date(), nullable=False),
        sa.Column("closes_on", sa.Date(), nullable=False),
        sa.Column("closed_at", DT, nullable=True),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("created_at", DT, nullable=False),
        sa.Column("updated_at", DT, nullable=False),
        sa.CheckConstraint("kind IN ('job', 'training', 'other')", name=op.f("ck_opening_kind")),
        sa.ForeignKeyConstraint(
            ["unit_id"], ["unit.id"], name=op.f("fk_opening_unit_id_unit"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_opening")),
    )
    with op.batch_alter_table("opening", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_opening_unit_id"), ["unit_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_opening_closes_on"), ["closes_on"], unique=False)
    op.create_table(
        "theme",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("unit_id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("created_at", DT, nullable=False),
        sa.Column("updated_at", DT, nullable=False),
        sa.CheckConstraint("kind IN ('theme', 'project')", name=op.f("ck_theme_kind")),
        sa.CheckConstraint("status IN ('active', 'archived')", name=op.f("ck_theme_status")),
        sa.ForeignKeyConstraint(
            ["unit_id"], ["unit.id"], name=op.f("fk_theme_unit_id_unit"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_theme")),
    )
    with op.batch_alter_table("theme", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_theme_unit_id"), ["unit_id"], unique=False)
    op.create_table(
        "theme_link",
        sa.Column("theme_id", sa.String(length=40), nullable=False),
        sa.Column("ref", sa.String(length=120), nullable=False),
        sa.Column("added_by", sa.String(length=200), nullable=False),
        sa.Column("added_at", DT, nullable=False),
        sa.ForeignKeyConstraint(
            ["theme_id"],
            ["theme.id"],
            name=op.f("fk_theme_link_theme_id_theme"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("theme_id", "ref", name=op.f("pk_theme_link")),
    )
    op.create_table(
        "group_inbox",
        sa.Column("unit_id", sa.String(length=40), nullable=False),
        sa.Column("reviewed_at", DT, nullable=False),
        sa.Column("reviewed_by", sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(
            ["unit_id"], ["unit.id"], name=op.f("fk_group_inbox_unit_id_unit"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("unit_id", name=op.f("pk_group_inbox")),
    )


def downgrade() -> None:
    op.drop_table("group_inbox")
    op.drop_table("theme_link")
    with op.batch_alter_table("theme", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_theme_unit_id"))
    op.drop_table("theme")
    with op.batch_alter_table("opening", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_opening_closes_on"))
        batch_op.drop_index(batch_op.f("ix_opening_unit_id"))
    op.drop_table("opening")
