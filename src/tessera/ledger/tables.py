"""Ledger schema (SQLAlchemy Core). The source of truth for migrations.

See spec/data-model.md. The ledger holds what humans decide or own; bulk source
content lives in the lake and is referenced by key.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    false,
    true,
)

from tessera.ledger.types import JsonType, UtcDateTime

# Deterministic constraint names keep migrations identical across SQLite and PostgreSQL.
metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

ID = String(40)  # typed IDs such as per_0J8ZQ3W5T4K2M9B7XHRC1DGFNE
ENTITY_REF = String(120)  # typed IDs or natural keys such as pub:12345678

# "dismissed": a discovered candidate a curator decided is not a member. Kept (not deleted)
# so that discovery does not propose the same person again.
PERSON_STATUSES = ("active", "candidate", "former", "dismissed")
PERSON_ACTIONS = ("promote", "dismiss", "undo")
ASSERTION_STATUSES = ("suggested", "accepted", "rejected", "deferred")
ASSERTION_ORIGINS = ("source", "inferred", "human")
RUN_STATUSES = ("running", "succeeded", "partial", "failed")


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


person = Table(
    "person",
    metadata,
    Column("id", ID, primary_key=True),
    Column("display_name", String(300), nullable=False),
    Column("family_name", String(200)),
    Column("given_name", String(200)),
    Column("status", String(16), nullable=False, server_default="active"),
    Column("created_at", UtcDateTime, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    CheckConstraint(_in("status", PERSON_STATUSES), name="status"),
)
Index(None, person.c.family_name)
Index(None, person.c.status)

person_name = Table(
    "person_name",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("person_id", ID, ForeignKey("person.id", ondelete="CASCADE"), nullable=False),
    Column("family", String(200), nullable=False),
    Column("given", String(200), nullable=False, server_default=""),
    Column("initials", String(20), nullable=False, server_default=""),
    Column("kind", String(16), nullable=False, server_default="variant"),
    Column("source", String(40), nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    UniqueConstraint("person_id", "family", "given", "kind"),
    CheckConstraint(_in("kind", ("primary", "variant", "former")), name="kind"),
)
Index(None, person_name.c.family)

person_identifier = Table(
    "person_identifier",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("person_id", ID, ForeignKey("person.id", ondelete="CASCADE"), nullable=False),
    Column("scheme", String(20), nullable=False),
    Column("value", String(320), nullable=False),
    Column("status", String(16), nullable=False, server_default="accepted"),
    Column("source", String(40), nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    UniqueConstraint("person_id", "scheme", "value"),
    CheckConstraint(_in("scheme", ("orcid", "nih_profile", "email", "local")), name="scheme"),
    CheckConstraint(_in("status", ASSERTION_STATUSES), name="status"),
)
Index(None, person_identifier.c.scheme, person_identifier.c.value)

unit = Table(
    "unit",
    metadata,
    Column("id", ID, primary_key=True),
    Column("name", String(300), nullable=False),
    Column("kind", String(40), nullable=False, server_default="department"),
    Column("parent_id", ID, ForeignKey("unit.id", ondelete="SET NULL")),
    Column("code", String(80), unique=True),
    Column("created_at", UtcDateTime, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
)
Index(None, unit.c.parent_id)

assertion = Table(
    "assertion",
    metadata,
    Column("id", ID, primary_key=True),
    Column("subject_type", String(20), nullable=False),
    Column("subject_id", ENTITY_REF, nullable=False),
    Column("predicate", String(40), nullable=False),
    Column("object_type", String(20), nullable=False),
    Column("object_id", ENTITY_REF, nullable=False),
    # Distinguishes otherwise-identical links, e.g. the author position for `authored`.
    Column("qualifier_key", String(80), nullable=False, server_default=""),
    Column("qualifiers", JsonType, nullable=False),
    Column("origin", String(12), nullable=False),
    Column("method", String(60), nullable=False),
    Column("score", Float),
    Column("evidence", JsonType, nullable=False),
    Column("status", String(16), nullable=False),
    Column("locked", Boolean, nullable=False, server_default=false()),
    # False once the source that reported this link stops reporting it.
    Column("reported", Boolean, nullable=False, server_default=true()),
    Column("decided_by", String(200)),
    Column("decided_at", UtcDateTime),
    Column("decision_note", Text),
    Column("first_seen_at", UtcDateTime, nullable=False),
    Column("last_evaluated_at", UtcDateTime, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    UniqueConstraint("subject_id", "predicate", "object_id", "qualifier_key"),
    CheckConstraint(_in("status", ASSERTION_STATUSES), name="status"),
    CheckConstraint(_in("origin", ASSERTION_ORIGINS), name="origin"),
    CheckConstraint("score IS NULL OR (score >= 0 AND score <= 1)", name="score_range"),
)
Index(None, assertion.c.subject_id, assertion.c.predicate)
Index(None, assertion.c.object_id, assertion.c.predicate)
Index(None, assertion.c.predicate, assertion.c.status)

curation_event = Table(
    "curation_event",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("assertion_id", ID, ForeignKey("assertion.id", ondelete="CASCADE"), nullable=False),
    Column("actor", String(200), nullable=False),
    Column("action", String(20), nullable=False),
    Column("from_status", String(16)),
    Column("to_status", String(16)),
    Column("note", Text),
    Column("at", UtcDateTime, nullable=False),
)
Index(None, curation_event.c.assertion_id)
Index(None, curation_event.c.at)

# Decisions about people (promote a discovered candidate, dismiss one), append-only like
# curation_event so that every change can be traced and undone.
person_event = Table(
    "person_event",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("person_id", ID, ForeignKey("person.id", ondelete="CASCADE"), nullable=False),
    Column("actor", String(200), nullable=False),
    Column("action", String(20), nullable=False),
    Column("from_status", String(16)),
    Column("to_status", String(16)),
    Column("note", Text),
    Column("at", UtcDateTime, nullable=False),
    CheckConstraint(_in("action", PERSON_ACTIONS), name="action"),
)
Index(None, person_event.c.person_id)
Index(None, person_event.c.at)

ingest_run = Table(
    "ingest_run",
    metadata,
    Column("id", ID, primary_key=True),
    Column("source", String(40), nullable=False),
    Column("kind", String(40), nullable=False),
    Column("status", String(16), nullable=False),
    Column("started_at", UtcDateTime, nullable=False),
    Column("finished_at", UtcDateTime),
    Column("params", JsonType, nullable=False),
    Column("counts", JsonType, nullable=False),
    Column("error", Text),
    CheckConstraint(_in("status", RUN_STATUSES), name="status"),
)
Index(None, ingest_run.c.source, ingest_run.c.started_at)

source_checkpoint = Table(
    "source_checkpoint",
    metadata,
    Column("source", String(40), primary_key=True),
    Column("scope", String(200), primary_key=True),
    Column("watermark", String(64), nullable=False),
    Column("run_id", ID),
    Column("updated_at", UtcDateTime, nullable=False),
)

setting = Table(
    "setting",
    metadata,
    Column("key", String(120), primary_key=True),
    Column("value", JsonType, nullable=False),
    Column("updated_by", String(200), nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
)
