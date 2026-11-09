"""Decisions hold (wave 8's review of the code that decides): a resolve never trips over a link
a person added, merges are undone only when that is safe, a person's undo undoes a decision
and nothing else, batches never overwrite a decision made meanwhile, a decision on a merged
record claims nothing, a decided author position stays, and a roster imported twice makes
each person once."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import insert, select

from tessera.core.clock import utc_now
from tessera.core.ids import new_id
from tessera.evidence import store
from tessera.evidence.merge import MergeError, merge_people, unmerge
from tessera.evidence.names import make_name
from tessera.evidence.people import import_roster
from tessera.ledger import migrate
from tessera.ledger.db import make_engine
from tessera.ledger.tables import assertion, person


@pytest.fixture
def engine(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = make_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
    migrate.upgrade(engine)
    yield engine
    engine.dispose()


def add_person(engine, family: str = "Okafor", given: str = "Ada", status: str = "active") -> str:  # type: ignore[no-untyped-def]
    with engine.begin() as conn:
        return store.create_person(conn, make_name(family, given), status=status, source="roster")


def add_authored(engine, person_id: str, pmid: str, status: str = "suggested",  # type: ignore[no-untyped-def]
                 position: int = 1, origin: str = "inferred") -> str:  # fmt: skip
    now = utc_now()
    aid = new_id("asr")
    with engine.begin() as conn:
        conn.execute(insert(assertion).values(
            id=aid, subject_type="person", subject_id=person_id, predicate="authored",
            object_type="publication", object_id=f"pub:{pmid}", qualifier_key="",
            qualifiers={"author_position": position}, origin=origin, method="authorship/v1.2",
            score=0.9, evidence=[], status="suggested", locked=False, reported=True,
            first_seen_at=now, last_evaluated_at=now, updated_at=now))  # fmt: skip
    if status != "suggested":
        store.decide(engine, aid, {"accepted": "accept", "rejected": "reject"}[status], "cur")
    return aid


def row(engine, aid: str) -> dict:  # type: ignore[no-untyped-def,type-arg]
    with engine.connect() as conn:
        return dict(conn.execute(select(assertion).where(assertion.c.id == aid)).mappings().one())


def draft(person_id: str, pmid: str, position: int = 2) -> store.Draft:
    return store.Draft(subject_type="person", subject_id=person_id, predicate="authored",
                       object_type="publication", object_id=f"pub:{pmid}", origin="inferred",
                       method="authorship/v1.2", score=0.95, evidence=[],
                       qualifiers={"author_position": position})  # fmt: skip


def test_a_resolve_leaves_a_link_a_person_added_as_it_is(engine) -> None:  # type: ignore[no-untyped-def]
    ada = add_person(engine)
    added = add_authored(engine, ada, "100", "accepted", origin="human")
    stats = store.sync_assertions(engine, "authored", [draft(ada, "100")], origin="inferred")
    assert stats.held == 1 and stats.inserted == 0
    assert row(engine, added)["origin"] == "human" and row(engine, added)["status"] == "accepted"


def test_a_decided_author_position_stays(engine) -> None:  # type: ignore[no-untyped-def]
    ada = add_person(engine)
    accepted = add_authored(engine, ada, "100", "accepted", position=1)
    store.sync_assertions(engine, "authored", [draft(ada, "100", position=3)], origin="inferred")
    assert row(engine, accepted)["qualifiers"] == {"author_position": 1}
    suggested = add_authored(engine, ada, "200", position=1)
    store.sync_assertions(engine, "authored", [draft(ada, "200", position=3)], origin="inferred",
                          stale_decided=False)  # fmt: skip
    assert row(engine, suggested)["qualifiers"] == {"author_position": 3}  # undecided: follows


def test_merges_are_undone_in_order_and_only_while_untouched(engine) -> None:  # type: ignore[no-untyped-def]
    a, b, d = add_person(engine), add_person(engine), add_person(engine)
    xa = add_authored(engine, a, "100", "accepted")
    add_authored(engine, d, "100", "rejected")
    first = merge_people(engine, a, b, "cur")
    second = merge_people(engine, d, b, "cur")
    with pytest.raises(MergeError, match=r"merged into .* since"):
        unmerge(engine, first["merge_id"], "cur")  # the later one first
    unmerge(engine, second["merge_id"], "cur")
    unmerge(engine, first["merge_id"], "cur")
    assert row(engine, xa)["subject_id"] == a  # back where it was
    # A decision made on a moved link since: undoing the merge takes the link back, decision
    # and all (a merge records only what it changed: whose link it is).
    moved = merge_people(engine, a, b, "cur")
    store.decide(engine, xa, "reject", "cur")
    unmerge(engine, moved["merge_id"], "cur")
    assert (row(engine, xa)["subject_id"], row(engine, xa)["status"]) == (a, "rejected")
    # What a merge changed, changed again since by something else: it is not undone blindly.
    again = merge_people(engine, a, b, "cur")
    elsewhere = add_person(engine, "Lin", "Bo")
    with engine.begin() as conn:
        conn.execute(assertion.update().where(assertion.c.id == xa).values(subject_id=elsewhere))
    with pytest.raises(MergeError, match="changed again since"):
        unmerge(engine, again["merge_id"], "cur")


def test_a_persons_undo_undoes_a_decision_and_nothing_else(engine) -> None:  # type: ignore[no-untyped-def]
    a = add_person(engine, status="candidate")
    b = add_person(engine)
    store.decide_person(engine, a, "promote", "cur")
    merged = merge_people(engine, a, b, "cur")
    with pytest.raises(store.TesseraError, match="undo the merge first"):
        store.decide_person(engine, a, "undo", "cur")
    unmerge(engine, merged["merge_id"], "cur")
    store.decide_person(engine, a, "undo", "cur")  # the promotion, not the unmerge
    with engine.connect() as conn:
        status, into = conn.execute(select(person.c.status, person.c.merged_into)
                                    .where(person.c.id == a)).one()  # fmt: skip
    assert (status, into) == ("candidate", None)


def test_a_batch_never_overwrites_a_decision_made_meanwhile(engine) -> None:  # type: ignore[no-untyped-def]
    ada = add_person(engine)
    link = add_authored(engine, ada, "100")
    store.decide(engine, link, "reject", "curator")  # while a batch had it as a suggestion
    with pytest.raises(store.Superseded):
        store.decide(engine, link, "accept", "auto", expect="suggested")
    assert row(engine, link)["status"] == "rejected"
    with pytest.raises(store.Superseded):  # undoing a batch: only what it did itself
        store.decide(engine, link, "undo", "cur", expect="accepted", expect_actor="auto")


def test_a_roster_imported_twice_makes_each_person_once(engine, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    roster = tmp_path / "roster.csv"
    roster.write_text("name,unit\nAda Okafor,Lab A\nBo Lin,Lab B\nBo Lin,Lab C\n", "utf-8")
    import_roster(engine, roster)
    again = import_roster(engine, roster)
    people = store.load_people(engine, ("active", "former", "candidate"))
    assert sorted(p.display_name for p in people) == ["Ada Okafor", "Bo Lin"]
    assert again.created == 0
