"""Teams for an opportunity (INT-10).

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-09 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import tessera.ledger.types

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DT = tessera.ledger.types.UtcDateTime(timezone=True)
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "team",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("opportunity", sa.String(length=80), nullable=True),
        sa.Column("concepts", JSON, nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("shared_with", JSON, nullable=False),
        sa.Column("owner", sa.String(length=200), nullable=False),
        sa.Column("owner_name", sa.String(length=200), nullable=False),
        sa.Column("created_at", DT, nullable=False),
        sa.Column("updated_at", DT, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_team")),
    )
    with op.batch_alter_table("team", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_team_owner"), ["owner"], unique=False)
    op.create_table(
        "team_member",
        sa.Column("team_id", sa.String(length=40), nullable=False),
        sa.Column("person_id", sa.String(length=40), nullable=False),
        sa.Column("role", sa.String(length=20), server_default="collaborator", nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("added_by", sa.String(length=200), nullable=False),
        sa.Column("added_at", DT, nullable=False),
        sa.CheckConstraint(
            "role IN ('lead', 'co_investigator', 'collaborator', 'core', 'other')",
            name=op.f("ck_team_member_role"),
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["person.id"],
            name=op.f("fk_team_member_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["team.id"],
            name=op.f("fk_team_member_team_id_team"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("team_id", "person_id", name=op.f("pk_team_member")),
    )
    with op.batch_alter_table("team_member", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_team_member_person_id"), ["person_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("team_member", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_team_member_person_id"))
    op.drop_table("team_member")
    with op.batch_alter_table("team", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_team_owner"))
    op.drop_table("team")
