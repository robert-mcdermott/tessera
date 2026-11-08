from __future__ import annotations

import pytest
from sqlalchemy import select

from tessera.core.errors import TesseraError
from tessera.evidence import store
from tessera.evidence.names import make_name
from tessera.ledger.db import make_engine
from tessera.ledger.tables import curation_event

from .conftest import Instance


@pytest.fixture
def engine(instance: Instance):  # type: ignore[no-untyped-def]
    engine = make_engine(instance.config.ledger_url)
    yield engine
    engine.dispose()


def draft(obj: str, score: float, feature: str = "org_affiliation") -> store.Draft:
    return store.Draft(
        "person",
        "per_X",
        "authored",
        "publication",
        f"pub:{obj}",
        origin="inferred",
        method="authorship/v1",
        score=score,
        evidence=[
            {
                "feature": feature,
                "value": None,
                "weight": 1.0,
                "explanation": "why",
                "source_ref": "x",
            }
        ],
    )


def by_object(engine) -> dict[str, dict]:  # type: ignore[no-untyped-def]
    return {r["object_id"]: r for r in store.assertions_for(engine, reported_only=False)}


def test_people_identifiers_and_names(engine) -> None:  # type: ignore[no-untyped-def]
    with engine.begin() as conn:
        pid = store.create_person(
            conn, make_name("Okafor", "Ada B"), status="active", source="test"
        )
        assert store.add_identifier(conn, pid, "orcid", "0000-0001-0000-0001", source="test")
        assert not store.add_identifier(conn, pid, "orcid", "0000-0001-0000-0001", source="test")
        assert not store.add_name(
            conn, pid, make_name("Okafor", "Ada B"), kind="variant", source="test"
        )
        assert store.add_name(conn, pid, make_name("Okafor", "A"), kind="variant", source="test")
        assert store.find_by_identifier(conn, "orcid", "0000-0001-0000-0001") == pid
        unit_a = store.get_or_create_unit(conn, "Medicine", code="MED")
        assert store.get_or_create_unit(conn, "Medicine (renamed)", code="MED") == unit_a
    [record] = store.load_people(engine, ("active",))
    assert record.display_name == "Ada B Okafor"
    assert {n.given for n in record.names} == {"Ada B", "A"}
    assert record.ids("orcid") == {"0000-0001-0000-0001"}


def test_sync_inserts_updates_and_marks_unreported(engine) -> None:  # type: ignore[no-untyped-def]
    stats = store.sync_assertions(
        engine,
        "authored",
        [draft("1", 0.9), draft("2", 0.01)],
        origin="inferred",
        min_score_for_new=0.05,
    )
    assert (stats.inserted, stats.skipped_low) == (1, 1)
    stats = store.sync_assertions(engine, "authored", [draft("1", 0.9)], origin="inferred")
    assert stats.unchanged == 1
    stats = store.sync_assertions(
        engine, "authored", [draft("1", 0.97, "grant_link"), draft("3", 0.5)], origin="inferred"
    )
    assert (stats.updated, stats.inserted) == (1, 1)
    assert by_object(engine)["pub:1"]["evidence"][0]["feature"] == "grant_link"
    stats = store.sync_assertions(engine, "authored", [draft("3", 0.5)], origin="inferred")
    assert stats.unreported == 1
    rows = by_object(engine)
    assert rows["pub:1"]["reported"] is False and rows["pub:3"]["reported"] is True
    stats = store.sync_assertions(
        engine, "authored", [draft("1", 0.97, "grant_link"), draft("3", 0.5)], origin="inferred"
    )
    assert stats.updated == 1 and by_object(engine)["pub:1"]["reported"] is True


def test_automation_never_changes_a_decision(engine) -> None:  # type: ignore[no-untyped-def]
    store.sync_assertions(engine, "authored", [draft("1", 0.9)], origin="inferred")
    aid = by_object(engine)["pub:1"]["id"]
    store.decide(engine, aid, "reject", "Curator", "not this person")
    store.sync_assertions(engine, "authored", [draft("1", 0.99, "orcid_match")], origin="inferred")
    row = by_object(engine)["pub:1"]
    assert (row["status"], row["locked"], row["score"]) == ("rejected", True, 0.99)
    assert row["decided_by"] == "Curator" and row["decision_note"] == "not this person"
    store.sync_assertions(engine, "authored", [], origin="inferred")
    row = by_object(engine)["pub:1"]
    assert row["status"] == "rejected" and row["reported"] is False  # marked, not deleted


def test_decisions_and_undo_stack(engine) -> None:  # type: ignore[no-untyped-def]
    store.sync_assertions(engine, "authored", [draft("1", 0.9)], origin="inferred")
    aid = by_object(engine)["pub:1"]["id"]
    assert store.decide(engine, aid, "accept", "A")["status"] == "accepted"
    assert store.decide(engine, aid, "reject", "B")["status"] == "rejected"
    assert store.decide(engine, aid, "undo", "B") == {
        "id": aid,
        "status": "accepted",
        "locked": True,
    }
    assert store.decide(engine, aid, "undo", "A") == {
        "id": aid,
        "status": "suggested",
        "locked": False,
    }
    with pytest.raises(TesseraError, match="Nothing to undo"):
        store.decide(engine, aid, "undo", "A")
    assert store.decide(engine, aid, "defer", "A")["locked"] is False
    with pytest.raises(TesseraError, match="Unknown action"):
        store.decide(engine, aid, "maybe", "A")
    with pytest.raises(TesseraError, match="No assertion"):
        store.decide(engine, "asr_nope", "accept", "A")
    with engine.connect() as conn:
        actions = [
            r.action for r in conn.execute(select(curation_event).order_by(curation_event.c.id))
        ]
    assert actions == ["accept", "reject", "undo", "undo", "defer"]


def test_drafts_must_match_predicate(engine) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(TesseraError):
        store.sync_assertions(engine, "official_of", [draft("1", 0.5)], origin="inferred")
