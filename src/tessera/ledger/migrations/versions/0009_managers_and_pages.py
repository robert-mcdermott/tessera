"""Managers and group pages: a ``manager`` role limited to a group (optionally with the groups
below it), each group's page as a draft and a published version made of blocks, and every
published version kept for history and rollback.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-09 02:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import tessera.ledger.types

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DT = tessera.ledger.types.UtcDateTime(timezone=True)
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
OLD_ROLES = "role IN ('admin', 'curator', 'researcher', 'viewer')"
NEW_ROLES = "role IN ('admin', 'curator', 'manager', 'researcher', 'viewer')"


def upgrade() -> None:
    with op.batch_alter_table("role_grant", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("ck_role_grant_role"), type_="check")
        batch_op.create_check_constraint(batch_op.f("ck_role_grant_role"), NEW_ROLES)
        batch_op.add_column(
            sa.Column("include_below", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
    op.create_table(
        "group_page",
        sa.Column("unit_id", sa.String(length=40), nullable=False),
        sa.Column("draft", JSON, nullable=True),
        sa.Column("draft_updated_at", DT, nullable=True),
        sa.Column("draft_updated_by", sa.String(length=200), nullable=True),
        sa.Column("published", JSON, nullable=True),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("published_at", DT, nullable=True),
        sa.Column("published_by", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(
            ["unit_id"], ["unit.id"], name=op.f("fk_group_page_unit_id_unit"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("unit_id", name=op.f("pk_group_page")),
    )
    op.create_table(
        "group_page_revision",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("unit_id", sa.String(length=40), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("blocks", JSON, nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("at", DT, nullable=False),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            ["unit.id"],
            name=op.f("fk_group_page_revision_unit_id_unit"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_group_page_revision")),
        sa.UniqueConstraint(
            "unit_id", "number", name=op.f("uq_group_page_revision_unit_id_number")
        ),
    )


def downgrade() -> None:
    op.drop_table("group_page_revision")
    op.drop_table("group_page")
    op.execute(sa.text("DELETE FROM role_grant WHERE role = 'manager'"))
    with op.batch_alter_table("role_grant", schema=None) as batch_op:
        batch_op.drop_column("include_below")
        batch_op.drop_constraint(batch_op.f("ck_role_grant_role"), type_="check")
        batch_op.create_check_constraint(batch_op.f("ck_role_grant_role"), OLD_ROLES)
