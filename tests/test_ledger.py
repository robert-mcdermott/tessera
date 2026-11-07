from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta, timezone

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import func, insert, inspect, select, text
from sqlalchemy.exc import IntegrityError

from tessera.core.errors import LedgerError
from tessera.core.ids import new_id
from tessera.ledger import migrate
from tessera.ledger.db import make_engine
from tessera.ledger.tables import (
    assertion,
    curation_event,
    metadata,
    person,
    person_event,
    person_identifier,
    person_name,
)

PG_URL = os.environ.get("TESSERA_TEST_POSTGRES_URL")


def _now() -> datetime:
    return datetime.now(UTC)


def _assertion_row(**overrides):  # type: ignore[no-untyped-def]
    now = _now()
    row = {
        "id": new_id("asr"),
        "subject_type": "person",
        "subject_id": new_id("per"),
        "predicate": "authored",
        "object_type": "publication",
        "object_id": "pub:12345678",
        "qualifier_key": "3",
        "qualifiers": {"author_position": 3},
        "origin": "inferred",
        "method": "authorship/v1",
        "score": 0.97,
        "evidence": [{"feature": "affiliation_match", "weight": 2.5}],
        "status": "suggested",
        "first_seen_at": now,
        "last_evaluated_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    return row


def _exercise(engine) -> None:  # type: ignore[no-untyped-def]
    """Behavior every ledger backend must share."""
    migrate.upgrade(engine)
    assert migrate.is_current(engine)
    with engine.connect() as conn:
        drift = compare_metadata(
            MigrationContext.configure(conn, opts={"compare_type": True}), metadata
        )
    assert drift == [], f"tables.py and migrations disagree: {drift}"

    pacific = timezone(timedelta(hours=-8))
    local = datetime(2026, 11, 7, 9, 30, tzinfo=pacific)
    row = _assertion_row(first_seen_at=local)
    with engine.begin() as conn:
        conn.execute(insert(assertion).values(**row))
        stored = conn.execute(select(assertion).where(assertion.c.id == row["id"])).mappings().one()
        assert stored["first_seen_at"] == local
        assert stored["first_seen_at"].tzinfo is not None
        assert stored["first_seen_at"].utcoffset() == timedelta(0)
        assert stored["locked"] is False
        assert stored["reported"] is True
        assert stored["qualifiers"] == {"author_position": 3}
        assert stored["evidence"][0]["feature"] == "affiliation_match"
        conn.execute(
            insert(curation_event).values(
                assertion_id=row["id"],
                actor="Operator",
                action="accept",
                from_status="suggested",
                to_status="accepted",
                at=_now(),
            )
        )

    with pytest.raises(IntegrityError), engine.begin() as conn:  # duplicate link
        conn.execute(insert(assertion).values(**{**row, "id": new_id("asr")}))
    with pytest.raises(IntegrityError), engine.begin() as conn:  # invalid status
        conn.execute(insert(assertion).values(**_assertion_row(status="maybe")))
    with pytest.raises(IntegrityError), engine.begin() as conn:  # score out of range
        conn.execute(insert(assertion).values(**_assertion_row(score=1.5)))
    with pytest.raises(IntegrityError), engine.begin() as conn:  # dangling foreign key
        conn.execute(
            insert(curation_event).values(
                assertion_id="asr_missing", actor="x", action="accept", at=_now()
            )
        )

    # 0002: dismissed people and person events, and a clean round trip through downgrade.
    pid = new_id("per")
    with engine.begin() as conn:
        conn.execute(
            insert(person).values(
                id=pid,
                display_name="Ada Example",
                status="dismissed",
                created_at=_now(),
                updated_at=_now(),
            )
        )
        conn.execute(
            insert(person_event).values(
                person_id=pid,
                actor="Operator",
                action="dismiss",
                from_status="candidate",
                to_status="dismissed",
                at=_now(),
            )
        )
    with pytest.raises(IntegrityError), engine.begin() as conn:  # unknown person action
        conn.execute(
            insert(person_event).values(person_id=pid, actor="x", action="delete", at=_now())
        )
    migrate.downgrade(engine, "0001")
    with engine.connect() as conn:
        assert (
            conn.execute(select(person.c.status).where(person.c.id == pid)).scalar() == "candidate"
        )
        assert "person_event" not in inspect(conn).get_table_names()
    migrate.upgrade(engine)
    with engine.connect() as conn:
        drift = compare_metadata(
            MigrationContext.configure(conn, opts={"compare_type": True}), metadata
        )
    assert drift == []


def _migrations_keep_rows(engine) -> None:  # type: ignore[no-untyped-def]
    """Upgrading and downgrading must never lose rows that reference a rebuilt table."""
    migrate.upgrade(engine, "0001")
    pid = new_id("per")
    now = _now()
    row = _assertion_row(subject_id=pid)
    with engine.begin() as conn:
        conn.execute(
            insert(person).values(
                id=pid,
                display_name="Ada Example",
                status="candidate",
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(
            insert(person_name).values(
                person_id=pid,
                family="Example",
                given="Ada",
                kind="primary",
                source="test",
                created_at=now,
            )
        )
        conn.execute(
            insert(person_identifier).values(
                person_id=pid,
                scheme="orcid",
                value="0000-0002-1825-0097",
                source="test",
                created_at=now,
                updated_at=now,
            )
        )
        conn.execute(insert(assertion).values(**row))
        conn.execute(
            insert(curation_event).values(
                assertion_id=row["id"], actor="Operator", action="accept", at=now
            )
        )

    def counts() -> dict[str, int]:
        with engine.connect() as conn:
            return {
                t.name: conn.execute(select(func.count()).select_from(t)).scalar()
                for t in (person, person_name, person_identifier, assertion, curation_event)
            }

    before = counts()
    assert all(before.values()), before
    migrate.upgrade(engine)
    assert counts() == before
    migrate.downgrade(engine, "0001")
    assert counts() == before
    migrate.upgrade(engine)
    assert counts() == before


def test_sqlite_migrations_keep_related_rows(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = make_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
    try:
        _migrations_keep_rows(engine)
        with engine.connect() as conn:  # foreign keys are back on afterwards
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
    finally:
        engine.dispose()


def test_sqlite_ledger(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = make_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
    try:
        assert migrate.current_revision(engine) is None
        _exercise(engine)
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        expected = set(metadata.tables) | {"alembic_version"}
        assert set(inspect(engine).get_table_names()) == expected
    finally:
        engine.dispose()


def test_naive_datetimes_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = make_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
    migrate.upgrade(engine)
    now = datetime.now(UTC)
    with pytest.raises(Exception, match="timezone-aware"), engine.begin() as conn:
        conn.execute(
            insert(person).values(
                id=new_id("per"),
                display_name="A. Person",
                created_at=now.replace(tzinfo=None),
                updated_at=now,
            )
        )
    engine.dispose()


def test_missing_sqlite_directory_is_a_clear_error(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(LedgerError, match="tessera init"):
        make_engine(f"sqlite:///{tmp_path / 'nope' / 'ledger.db'}")


def test_unsupported_url() -> None:
    with pytest.raises(LedgerError):
        make_engine("mysql://localhost/x")


@pytest.mark.postgres
@pytest.mark.skipif(not PG_URL, reason="set TESSERA_TEST_POSTGRES_URL to run PostgreSQL tests")
def test_postgres_ledger() -> None:
    engine = make_engine(PG_URL or "")
    try:
        for check in (_migrations_keep_rows, _exercise):
            with engine.begin() as conn:  # start from an empty schema
                conn.execute(text("DROP SCHEMA public CASCADE"))
                conn.execute(text("CREATE SCHEMA public"))
            check(engine)
        with engine.connect() as conn:
            kind = conn.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name = 'assertion' AND column_name = 'evidence'"
                )
            ).scalar()
        assert kind == "jsonb"
    finally:
        engine.dispose()
