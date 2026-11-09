"""CVs and NIH biosketches: named versions of a person's documents (wave 6).

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-09 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import tessera.ledger.types

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DT = tessera.ledger.types.UtcDateTime(timezone=True)
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "cv_document",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("person_id", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("spec", JSON, nullable=False),
        sa.Column("form", sa.String(length=60), nullable=True),
        sa.Column("created_at", DT, nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("updated_at", DT, nullable=False),
        sa.Column("updated_by", sa.String(length=200), nullable=False),
        sa.CheckConstraint("kind IN ('cv', 'nih_biosketch')", name=op.f("ck_cv_document_kind")),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["person.id"],
            name=op.f("fk_cv_document_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cv_document")),
    )
    with op.batch_alter_table("cv_document", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_cv_document_person_id"), ["person_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("cv_document", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_cv_document_person_id"))
    op.drop_table("cv_document")
