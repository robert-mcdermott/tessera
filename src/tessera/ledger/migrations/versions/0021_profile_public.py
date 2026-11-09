"""What a person's public page shows: sections and entries they leave off it (wave 6).

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-09 10:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import tessera.ledger.types

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DT = tessera.ledger.types.UtcDateTime(timezone=True)
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("profile_entry", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("hidden", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
    op.create_table(
        "profile_public",
        sa.Column("person_id", sa.String(length=40), nullable=False),
        sa.Column("hidden_sections", JSON, nullable=False),
        sa.Column("updated_at", DT, nullable=False),
        sa.Column("updated_by", sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["person.id"],
            name=op.f("fk_profile_public_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("person_id", name=op.f("pk_profile_public")),
    )


def downgrade() -> None:
    op.drop_table("profile_public")
    with op.batch_alter_table("profile_entry", schema=None) as batch_op:
        batch_op.drop_column("hidden")
