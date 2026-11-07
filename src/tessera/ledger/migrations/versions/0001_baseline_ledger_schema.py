"""Baseline ledger schema: people, units, assertions, curation, runs, settings.

Revision ID: 0001
Revises:
Create Date: 2026-11-07 11:13:03.355503
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import tessera.ledger.types

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assertion",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("subject_type", sa.String(length=20), nullable=False),
        sa.Column("subject_id", sa.String(length=120), nullable=False),
        sa.Column("predicate", sa.String(length=40), nullable=False),
        sa.Column("object_type", sa.String(length=20), nullable=False),
        sa.Column("object_id", sa.String(length=120), nullable=False),
        sa.Column("qualifier_key", sa.String(length=80), server_default="", nullable=False),
        sa.Column(
            "qualifiers",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("origin", sa.String(length=12), nullable=False),
        sa.Column("method", sa.String(length=60), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column(
            "evidence",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("locked", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("reported", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("decided_by", sa.String(length=200), nullable=True),
        sa.Column("decided_at", tessera.ledger.types.UtcDateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("first_seen_at", tessera.ledger.types.UtcDateTime(timezone=True), nullable=False),
        sa.Column(
            "last_evaluated_at", tessera.ledger.types.UtcDateTime(timezone=True), nullable=False
        ),
        sa.Column("updated_at", tessera.ledger.types.UtcDateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "origin IN ('source', 'inferred', 'human')", name=op.f("ck_assertion_origin")
        ),
        sa.CheckConstraint(
            "status IN ('suggested', 'accepted', 'rejected', 'deferred')",
            name=op.f("ck_assertion_status"),
        ),
        sa.CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 1)", name=op.f("ck_assertion_score_range")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assertion")),
        sa.UniqueConstraint(
            "subject_id",
            "predicate",
            "object_id",
            "qualifier_key",
            name=op.f("uq_assertion_subject_id_predicate_object_id_qualifier_key"),
        ),
    )
    with op.batch_alter_table("assertion", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_assertion_object_id_predicate"), ["object_id", "predicate"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_assertion_predicate_status"), ["predicate", "status"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_assertion_subject_id_predicate"),
            ["subject_id", "predicate"],
            unique=False,
        )

    op.create_table(
        "ingest_run",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", tessera.ledger.types.UtcDateTime(timezone=True), nullable=False),
        sa.Column("finished_at", tessera.ledger.types.UtcDateTime(timezone=True), nullable=True),
        sa.Column(
            "params",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "counts",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'partial', 'failed')",
            name=op.f("ck_ingest_run_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingest_run")),
    )
    with op.batch_alter_table("ingest_run", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ingest_run_source_started_at"), ["source", "started_at"], unique=False
        )

    op.create_table(
        "person",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("display_name", sa.String(length=300), nullable=False),
        sa.Column("family_name", sa.String(length=200), nullable=True),
        sa.Column("given_name", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("created_at", tessera.ledger.types.UtcDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", tessera.ledger.types.UtcDateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'candidate', 'former')", name=op.f("ck_person_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_person")),
    )
    with op.batch_alter_table("person", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_person_family_name"), ["family_name"], unique=False)
        batch_op.create_index(batch_op.f("ix_person_status"), ["status"], unique=False)

    op.create_table(
        "setting",
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column(
            "value",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.String(length=200), nullable=False),
        sa.Column("updated_at", tessera.ledger.types.UtcDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_setting")),
    )
    op.create_table(
        "source_checkpoint",
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("scope", sa.String(length=200), nullable=False),
        sa.Column("watermark", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=40), nullable=True),
        sa.Column("updated_at", tessera.ledger.types.UtcDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("source", "scope", name=op.f("pk_source_checkpoint")),
    )
    op.create_table(
        "unit",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("kind", sa.String(length=40), server_default="department", nullable=False),
        sa.Column("parent_id", sa.String(length=40), nullable=True),
        sa.Column("code", sa.String(length=80), nullable=True),
        sa.Column("created_at", tessera.ledger.types.UtcDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", tessera.ledger.types.UtcDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["unit.id"], name=op.f("fk_unit_parent_id_unit"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_unit")),
        sa.UniqueConstraint("code", name=op.f("uq_unit_code")),
    )
    with op.batch_alter_table("unit", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_unit_parent_id"), ["parent_id"], unique=False)

    op.create_table(
        "curation_event",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("assertion_id", sa.String(length=40), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("from_status", sa.String(length=16), nullable=True),
        sa.Column("to_status", sa.String(length=16), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("at", tessera.ledger.types.UtcDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["assertion_id"],
            ["assertion.id"],
            name=op.f("fk_curation_event_assertion_id_assertion"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_curation_event")),
    )
    with op.batch_alter_table("curation_event", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_curation_event_assertion_id"), ["assertion_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_curation_event_at"), ["at"], unique=False)

    op.create_table(
        "person_identifier",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("person_id", sa.String(length=40), nullable=False),
        sa.Column("scheme", sa.String(length=20), nullable=False),
        sa.Column("value", sa.String(length=320), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="accepted", nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("created_at", tessera.ledger.types.UtcDateTime(timezone=True), nullable=False),
        sa.Column("updated_at", tessera.ledger.types.UtcDateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scheme IN ('orcid', 'nih_profile', 'email', 'local')",
            name=op.f("ck_person_identifier_scheme"),
        ),
        sa.CheckConstraint(
            "status IN ('suggested', 'accepted', 'rejected', 'deferred')",
            name=op.f("ck_person_identifier_status"),
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["person.id"],
            name=op.f("fk_person_identifier_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_person_identifier")),
        sa.UniqueConstraint(
            "person_id", "scheme", "value", name=op.f("uq_person_identifier_person_id_scheme_value")
        ),
    )
    with op.batch_alter_table("person_identifier", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_person_identifier_scheme_value"), ["scheme", "value"], unique=False
        )

    op.create_table(
        "person_name",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("person_id", sa.String(length=40), nullable=False),
        sa.Column("family", sa.String(length=200), nullable=False),
        sa.Column("given", sa.String(length=200), server_default="", nullable=False),
        sa.Column("initials", sa.String(length=20), server_default="", nullable=False),
        sa.Column("kind", sa.String(length=16), server_default="variant", nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("created_at", tessera.ledger.types.UtcDateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('primary', 'variant', 'former')", name=op.f("ck_person_name_kind")
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["person.id"],
            name=op.f("fk_person_name_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_person_name")),
        sa.UniqueConstraint(
            "person_id",
            "family",
            "given",
            "kind",
            name=op.f("uq_person_name_person_id_family_given_kind"),
        ),
    )
    with op.batch_alter_table("person_name", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_person_name_family"), ["family"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("person_name", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_person_name_family"))

    op.drop_table("person_name")
    with op.batch_alter_table("person_identifier", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_person_identifier_scheme_value"))

    op.drop_table("person_identifier")
    with op.batch_alter_table("curation_event", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_curation_event_at"))
        batch_op.drop_index(batch_op.f("ix_curation_event_assertion_id"))

    op.drop_table("curation_event")
    with op.batch_alter_table("unit", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_unit_parent_id"))

    op.drop_table("unit")
    op.drop_table("source_checkpoint")
    op.drop_table("setting")
    with op.batch_alter_table("person", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_person_status"))
        batch_op.drop_index(batch_op.f("ix_person_family_name"))

    op.drop_table("person")
    with op.batch_alter_table("ingest_run", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ingest_run_source_started_at"))

    op.drop_table("ingest_run")
    with op.batch_alter_table("assertion", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_assertion_subject_id_predicate"))
        batch_op.drop_index(batch_op.f("ix_assertion_predicate_status"))
        batch_op.drop_index(batch_op.f("ix_assertion_object_id_predicate"))

    op.drop_table("assertion")
