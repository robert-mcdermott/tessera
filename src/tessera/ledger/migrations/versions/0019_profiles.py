"""The sections of a profile that a researcher keeps, and their history (wave 6).

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-09 06:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import tessera.ledger.types

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DT = tessera.ledger.types.UtcDateTime(timezone=True)
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
SECTIONS = (
    "'overview', 'interests', 'position', 'education', 'honor', 'membership', 'teaching', "
    "'talk', 'link', 'featured'"
)


def upgrade() -> None:
    op.create_table(
        "profile_entry",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("person_id", sa.String(length=40), nullable=False),
        sa.Column("section", sa.String(length=20), nullable=False),
        sa.Column("fields", JSON, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=40), server_default="manual", nullable=False),
        sa.Column("source_id", sa.String(length=300), nullable=True),
        sa.Column("created_at", DT, nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("updated_at", DT, nullable=False),
        sa.Column("updated_by", sa.String(length=200), nullable=False),
        sa.CheckConstraint(f"section IN ({SECTIONS})", name=op.f("ck_profile_entry_section")),
        sa.ForeignKeyConstraint(
            ["person_id"], ["person.id"], name=op.f("fk_profile_entry_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profile_entry")),
        sa.UniqueConstraint(
            "person_id", "source", "source_id",
            name=op.f("uq_profile_entry_person_id_source_source_id"),
        ),
    )  # fmt: skip
    with op.batch_alter_table("profile_entry", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_profile_entry_person_id_section"), ["person_id", "section"], unique=False
        )
    op.create_table(
        "profile_event",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("person_id", sa.String(length=40), nullable=False),
        sa.Column("entry_id", sa.String(length=40), nullable=True),
        sa.Column("section", sa.String(length=20), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=False),
        sa.Column("capacity", sa.String(length=20), nullable=False),
        sa.Column("before", JSON, nullable=True),
        sa.Column("after", JSON, nullable=True),
        sa.Column("at", DT, nullable=False),
        sa.ForeignKeyConstraint(
            ["person_id"], ["person.id"], name=op.f("fk_profile_event_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profile_event")),
    )  # fmt: skip
    with op.batch_alter_table("profile_event", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_profile_event_person_id_at"), ["person_id", "at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("profile_event", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_profile_event_person_id_at"))
    op.drop_table("profile_event")
    with op.batch_alter_table("profile_entry", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_profile_entry_person_id_section"))
    op.drop_table("profile_entry")
