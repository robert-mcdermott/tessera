"""Reversible person merges (REC-07) over a synthetic organization with duplicates."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine

from tessera.evidence import store
from tessera.evidence.merge import MergeError, active_merges, merge_people, unmerge
from tessera.evidence.people import import_roster
from tessera.evidence.resolve import resolve
from tessera.ledger.tables import (
    assertion,
    curation_event,
    person,
    person_identifier,
    person_name,
    unit,
)

from . import synthetic_org
from .conftest import Instance
from .test_evidence import TODAY, Scenario, make_scenario


@dataclass
class Duplicates:
    s: Scenario
    pairs: list[tuple[str, str, synthetic_org.Researcher]]  # (roster record, grant record, who)

    @property
    def engine(self) -> Engine:
        return self.s.engine


@pytest.fixture
def dups(instance: Instance, tmp_path: Path) -> Iterator[Duplicates]:
    s = make_scenario(instance, tmp_path)
    grant_ids = {r.key: s.person_id(r.key) for r in s.world.insiders}
    path, listed = synthetic_org.duplicate_roster(s.world, tmp_path)
    import_roster(s.engine, path)
    resolve(s.instance.config, s.instance.home, s.engine, today=TODAY)
    pairs = []
    for r in listed:
        records = [
            p.id
            for p in store.load_people(s.engine)
            if any(n.family == r.family for n in p.names) and p.id != grant_ids[r.key]
        ]
        assert len(records) == 1, r.family
        pairs.append((records[0], grant_ids[r.key], r))
    yield Duplicates(s, pairs)
    s.engine.dispose()


def snapshot(engine: Engine) -> dict[str, list[tuple]]:
    out = {}
    with engine.connect() as conn:
        for table in (person, person_name, person_identifier, unit, assertion, curation_event):
            rows = conn.execute(select(table)).all()
            out[table.name] = sorted(tuple(map(repr, r)) for r in rows)
    return out


def links(engine: Engine, person_id: str, predicate: str = "authored") -> dict[str, dict]:
    return {
        r["object_id"]: r
        for r in store.assertions_for(engine, subject_id=person_id)
        if r["predicate"] == predicate
    }


def test_merge_then_unmerge_restores_the_ledger_exactly(dups: Duplicates) -> None:
    roster_id, grant_id, who = dups.pairs[0]
    engine = dups.engine
    # Decisions on both records, including the same paper decided differently on each.
    roster_links, grant_links = links(engine, roster_id), links(engine, grant_id)
    shared = sorted(set(roster_links) & set(grant_links))
    assert shared, "duplicates should compete for the same papers"
    store.decide(engine, roster_links[shared[0]]["id"], "accept", "Curator A")
    store.decide(engine, grant_links[shared[0]]["id"], "reject", "Curator B")  # most recent
    only_roster = sorted(set(roster_links) - set(grant_links))
    if only_roster:
        store.decide(engine, roster_links[only_roster[0]]["id"], "accept", "Curator A")
    before = snapshot(engine)

    result = merge_people(
        engine, roster_id, grant_id, "Curator C", "Same person: roster and grants"
    )
    assert result["links"] > 0 and result["identifiers"] >= 1  # names are the same here
    assert [c["object_id"] for c in result["conflicts"]] == [shared[0]]
    merged = {p.id: p for p in store.load_people(engine, ("active", "former", "merged"))}
    assert merged[roster_id].status == "merged"
    assert who.email in merged[grant_id].ids("email")  # the roster's email moved over
    after = links(engine, grant_id)
    assert after[shared[0]]["status"] == "rejected"  # the more recent decision holds
    if only_roster:
        assert after[only_roster[0]]["status"] == "accepted"
    # The losing copy of the conflicting decision stays with the merged record, out of sight.
    left = [k for k, r in links(engine, roster_id).items() if r["status"] == "accepted"]
    assert left == [shared[0]]
    [record] = active_merges(engine, grant_id)
    assert (record["merged_id"], record["note"]) == (roster_id, "Same person: roster and grants")

    unmerge(engine, result["merge_id"], "Curator C")
    assert snapshot(engine) == before
    assert active_merges(engine, grant_id) == []


def test_merging_survives_a_resolve_and_unmerges_cleanly(dups: Duplicates) -> None:
    roster_id, grant_id, _ = dups.pairs[1]
    engine, s = dups.engine, dups.s
    accepted = next(iter(links(engine, roster_id).values()))
    store.decide(engine, accepted["id"], "accept", "Curator A")
    result = merge_people(engine, roster_id, grant_id, "Curator A")
    resolve(s.instance.config, s.instance.home, engine, today=TODAY)
    # The merged record is out of the directory, and its decision stands on the survivor.
    directory = {p.id for p in store.load_people(engine)}
    assert roster_id not in directory and grant_id in directory
    assert links(engine, grant_id)[accepted["object_id"]]["status"] == "accepted"
    unmerge(engine, result["merge_id"], "Curator A")
    resolve(s.instance.config, s.instance.home, engine, today=TODAY)
    assert links(engine, roster_id)[accepted["object_id"]]["status"] == "accepted"
    assert {roster_id, grant_id} <= {p.id for p in store.load_people(engine)}


def test_merges_that_cannot_be_done(dups: Duplicates) -> None:
    (a, b, _), (c, d, _) = dups.pairs[:2]
    engine = dups.engine
    with pytest.raises(MergeError, match="themselves"):
        merge_people(engine, a, a, "x")
    first = merge_people(engine, a, b, "x")
    with pytest.raises(MergeError, match="already been merged"):
        merge_people(engine, a, c, "x")
    later = merge_people(engine, b, d, "x")  # the survivor is folded into another record
    with pytest.raises(MergeError, match="Undo that merge first"):
        unmerge(engine, first["merge_id"], "x")
    unmerge(engine, later["merge_id"], "x")
    unmerge(engine, first["merge_id"], "x")
    with pytest.raises(MergeError, match="already been undone"):
        unmerge(engine, first["merge_id"], "x")


def test_identifiers_find_the_survivor(dups: Duplicates) -> None:
    roster_id, grant_id, who = dups.pairs[2]
    engine = dups.engine
    merge_people(engine, roster_id, grant_id, "x")
    with engine.connect() as conn:
        assert store.find_by_identifier(conn, "email", who.email) == grant_id
    # Importing the duplicate roster again finds the survivor instead of a new record.
    path, _ = synthetic_org.duplicate_roster(dups.s.world, dups.s.instance.path.parent)
    stats = import_roster(engine, path)
    assert stats.created == 0


# --- possible duplicates ---------------------------------------------------------------------


def proposals(engine: Engine, statuses: tuple[str, ...] = ("suggested",)) -> dict:
    return {
        frozenset((r["subject_id"], r["object_id"])): r
        for r in store.assertions_for(engine, predicate="same_as", statuses=statuses)
    }


def test_duplicates_are_proposed_and_nothing_else(dups: Duplicates) -> None:
    found = proposals(dups.engine)
    expected = {frozenset((roster_id, grant_id)) for roster_id, grant_id, _ in dups.pairs}
    assert set(found) == expected  # every duplicate, and no pair of different people
    for roster_id, grant_id, _ in dups.pairs:
        row = found[frozenset((roster_id, grant_id))]
        assert (row["subject_id"], row["object_id"]) == (roster_id, grant_id)  # keep the older
        assert row["score"] >= 0.95, row["evidence"]
        features = {f["feature"] for f in row["evidence"]}
        assert {"name_full", "shared_positions"} <= features


def test_a_rejected_pair_stays_rejected(dups: Duplicates) -> None:
    roster_id, grant_id, _ = dups.pairs[0]
    engine, s = dups.engine, dups.s
    row = proposals(engine)[frozenset((roster_id, grant_id))]
    store.decide(engine, row["id"], "reject", "Curator", "Two people")
    resolve(s.instance.config, s.instance.home, engine, today=TODAY)
    after = proposals(engine, ("rejected",))[frozenset((roster_id, grant_id))]
    assert after["id"] == row["id"] and after["status"] == "rejected"


def test_merging_a_proposal_accepts_it_and_unmerging_restores_it(dups: Duplicates) -> None:
    roster_id, grant_id, _ = dups.pairs[3]
    engine, s = dups.engine, dups.s
    row = proposals(engine)[frozenset((roster_id, grant_id))]
    result = merge_people(engine, roster_id, grant_id, "Curator")
    resolve(s.instance.config, s.instance.home, engine, today=TODAY)
    accepted = proposals(engine, ("accepted",))[frozenset((roster_id, grant_id))]
    assert accepted["id"] == row["id"] and accepted["locked"]
    unmerge(engine, result["merge_id"], "Curator")
    assert proposals(engine)[frozenset((roster_id, grant_id))]["status"] == "suggested"


# --- API ---------------------------------------------------------------------------------------


def test_duplicates_and_merges_over_the_api(dups: Duplicates) -> None:
    from fastapi.testclient import TestClient

    from tessera.api.app import create_app
    from tessera.jobs.build import build_search

    s = dups.s
    build_search(s.instance.config, s.instance.home, dups.engine)
    roster_id, grant_id, who = dups.pairs[0]
    with TestClient(create_app(s.instance.config), base_url="http://localhost") as client:
        body = client.get("/api/v1/duplicates").json()
        assert body["meta"]["total"] == len(dups.pairs)
        item = next(i for i in body["data"] if i["merge"]["id"] == roster_id)
        assert item["into"]["id"] == grant_id and item["band"] == "strong"
        assert item["merge"]["identifiers"] == [
            {"scheme": "email", "value": who.email, "status": "accepted"}
        ]
        assert item["merge"]["units"] and item["into"]["grants"] >= 1

        # Accepting a pair without merging is refused; merging accepts it.
        refused = client.post(
            f"/api/v1/assertions/{item['id']}/decision", json={"action": "accept"}
        )
        assert refused.status_code == 409 and "merge" in refused.json()["detail"]
        merged = client.post("/api/v1/merges", json={"merge": roster_id, "into": grant_id})
        assert merged.status_code == 200, merged.text
        merge_id = merged.json()["data"]["merge_id"]

        directory = {
            p["id"] for p in client.get("/api/v1/people", params={"limit": 200}).json()["data"]
        }
        assert roster_id not in directory and grant_id in directory
        survivor = client.get(f"/api/v1/people/{grant_id}").json()["data"]
        assert [m["merged_id"] for m in survivor["merged"]] == [roster_id]
        assert any(u["current"] and u["start"] == "2015" for u in survivor["units"])
        folded = client.get(f"/api/v1/people/{roster_id}").json()["data"]
        assert (folded["status"], folded["merged_into"]["id"]) == ("merged", grant_id)
        assert client.get("/api/v1/duplicates").json()["meta"]["total"] == len(dups.pairs) - 1

        again = client.post("/api/v1/merges", json={"merge": roster_id, "into": grant_id})
        assert again.status_code == 409
        undone = client.post(f"/api/v1/merges/{merge_id}/undo", json={"note": "Wrong person"})
        assert undone.status_code == 200, undone.text
        assert client.post(f"/api/v1/merges/{merge_id}/undo").status_code == 409
        assert client.post("/api/v1/merges/mrg_missing/undo").status_code == 404
        directory = {
            p["id"] for p in client.get("/api/v1/people", params={"limit": 200}).json()["data"]
        }
        assert {roster_id, grant_id} <= directory

        log = client.get("/api/v1/decisions", params={"limit": 2}).json()["data"]
        assert [e["action"] for e in log] == ["unmerge", "merge"]
        assert log[0]["merge"]["survivor"]["id"] == grant_id
