"""Curation over a synthetic organization: people decisions, batch decisions, the decision
log, and the search index staying current between rebuilds."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tessera.api.app import create_app
from tessera.config import Config, example_config_text, load_config
from tessera.evidence.people import import_roster
from tessera.evidence.resolve import resolve
from tessera.home import DataHome
from tessera.jobs.build import build_search
from tessera.ledger import migrate
from tessera.ledger.db import make_engine
from tessera.search import update
from tessera.search.index import index_path

from . import synthetic_org

TODAY = date(2026, 11, 7)


@dataclass
class Setup:
    client: TestClient
    world: synthetic_org.World
    config: Config
    home: DataHome


@pytest.fixture(scope="module")
def setup(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Setup]:
    tmp = tmp_path_factory.mktemp("curation")
    (tmp / "tessera.toml").write_text(example_config_text())
    config = load_config(tmp / "tessera.toml", env={})
    home = DataHome(config.data.home)
    home.create()
    engine = make_engine(config.ledger_url)
    migrate.upgrade(engine)
    world = synthetic_org.build(tmp, newcomers=3)
    synthetic_org.load(world, home.lake)
    import_roster(engine, world.roster_path)
    resolve(config, home, engine, today=TODAY)
    build_search(config, home, engine)
    engine.dispose()
    with TestClient(create_app(config)) as client:
        yield Setup(client, world, config, home)


def ok(response):  # type: ignore[no-untyped-def]
    assert response.status_code == 200, response.text
    return response.json()


def candidates(client: TestClient, **params) -> dict:  # type: ignore[no-untyped-def,type-arg]
    return ok(client.get("/api/v1/candidates", params=params))


def candidate_id(client: TestClient, family: str, status: str = "candidate") -> str:
    items = candidates(client, status=status, limit=50)["data"]
    return next(i["id"] for i in items if i["name"].endswith(family))


def directory_ids(client: TestClient) -> set[str]:
    return {p["id"] for p in ok(client.get("/api/v1/people", params={"limit": 200}))["data"]}


def people_decision(client: TestClient, ids: list[str], action: str, **extra):  # type: ignore[no-untyped-def]
    return client.post("/api/v1/candidates/decisions", json={"ids": ids, "action": action, **extra})


# --- discovered people ---------------------------------------------------------------------------


def test_candidates_list_with_evidence(setup: Setup) -> None:
    body = candidates(setup.client)
    assert body["meta"]["total"] == 3
    facets = {f["value"]: f["count"] for f in body["meta"]["facets"]["source"]}
    assert facets == {"publications": 2, "grants": 1}
    by_name = {i["name"].split()[-1]: i for i in body["data"]}
    newcomer = setup.world.newcomers[1]
    found = by_name[newcomer.family]
    assert found["papers"] == len(newcomer.papers) and found["recent"]
    assert {r["pmid"] for r in found["recent"]} <= set(newcomer.papers)
    assert "Example University" in found["affiliations"][0]
    assert setup.world.newcomers[-1].family not in by_name  # one paper is not enough
    co = by_name[setup.world.co_investigator.family]
    assert co["source"] == "grants" and co["grant_count"] == 1
    assert co["grants"][0]["core_project_num"] == setup.world.insiders[0].grant
    searched = candidates(setup.client, q=newcomer.family)["data"]
    assert [i["name"] for i in searched] == [found["name"]]
    # Candidates are not members: the directory and "everything" search leave them out.
    assert found["id"] not in directory_ids(setup.client)
    everything = ok(setup.client.get("/api/v1/search", params={"q": newcomer.family}))["data"]
    assert "candidate" not in everything


def test_promote_undo_and_dismiss(setup: Setup) -> None:
    client = setup.client
    newcomer = setup.world.newcomers[0]
    pid = candidate_id(client, newcomer.family)

    promoted = ok(people_decision(client, [pid], "promote", note="Confirmed by the department"))
    assert promoted["data"]["results"] == [{"id": pid, "status": "active", "previous": "candidate"}]
    assert pid in directory_ids(client)  # the index is updated in place, no rebuild
    profile = ok(client.get(f"/api/v1/people/{pid}"))["data"]
    assert profile["status"] == "active"
    assert pid not in {i["id"] for i in candidates(client)["data"]}
    assert pid in {i["id"] for i in candidates(client, status="active")["data"]}

    ok(people_decision(client, [pid], "undo"))
    assert pid not in directory_ids(client)
    assert pid in {i["id"] for i in candidates(client)["data"]}

    ok(people_decision(client, [pid], "dismiss"))
    assert pid in {i["id"] for i in candidates(client, status="dismissed")["data"]}
    assert people_decision(client, [pid], "dismiss").status_code == 409  # already dismissed

    log = ok(client.get("/api/v1/decisions", params={"limit": 3}))["data"]
    assert [e["action"] for e in log] == ["dismiss", "undo", "promote"]
    assert (
        log[0]["kind"] == "person"
        and log[0]["subject"]["label"] == f"{newcomer.given} {newcomer.middle} {newcomer.family}"
    )
    assert log[2]["note"] == "Confirmed by the department" and log[2]["actor"] == "Operator"


def test_dismissed_people_are_not_rediscovered(setup: Setup) -> None:
    client = setup.client
    newcomer = setup.world.newcomers[0]
    pid = candidate_id(client, newcomer.family, status="dismissed")
    engine = make_engine(setup.config.ledger_url)
    try:
        counts = resolve(setup.config, setup.home, engine, today=TODAY).counts
    finally:
        engine.dispose()
    assert counts["discovery"]["candidates_created"] == 0
    assert pid in {i["id"] for i in candidates(client, status="dismissed")["data"]}


def test_people_decisions_validate(setup: Setup) -> None:
    client = setup.client
    member = next(iter(directory_ids(client)))
    assert people_decision(client, [member], "dismiss").status_code == 409  # only candidates
    assert people_decision(client, ["per_missing"], "promote").status_code == 404
    assert people_decision(client, [member], "maybe").status_code == 422
    mixed = ok(people_decision(client, [member, "per_missing"], "dismiss"))["data"]
    assert mixed["decided"] == 0 and len(mixed["failed"]) == 2
    co = candidate_id(client, setup.world.co_investigator.family)
    both = ok(people_decision(client, [co, co], "promote"))["data"]  # duplicates collapse
    assert both["decided"] == 1
    ok(people_decision(client, [co], "undo"))


# --- links ---------------------------------------------------------------------------------------


def person_id(client: TestClient, family: str) -> str:
    items = ok(client.get("/api/v1/people", params={"q": family}))["data"]
    return next(i["id"] for i in items if i["name"].endswith(family))


def person_doc(client: TestClient, pid: str) -> dict:  # type: ignore[type-arg]
    return next(
        p for p in ok(client.get("/api/v1/people", params={"limit": 200}))["data"] if p["id"] == pid
    )


def test_decisions_update_search_in_place(setup: Setup) -> None:
    client = setup.client
    pid = person_id(client, setup.world.insiders[2].family)
    before = person_doc(client, pid)["counts"]
    suggestion = next(
        i
        for i in ok(client.get(f"/api/v1/people/{pid}/publications", params={"limit": 100}))["data"]
        if i["assertion"]["status"] == "suggested"
    )
    aid, pmid = suggestion["assertion"]["id"], suggestion["publication"]["pmid"]

    ok(client.post(f"/api/v1/assertions/{aid}/decision", json={"action": "accept"}))
    after = person_doc(client, pid)["counts"]
    assert after["accepted"] == before["accepted"] + 1
    assert after["suggested"] == before["suggested"] - 1
    record = ok(client.get(f"/api/v1/publications/{pmid}"))["data"]
    listed = ok(client.get("/api/v1/publications", params={"person": pid, "limit": 200}))["data"]
    summary = next(p for p in listed if p["pmid"] == pmid)
    assert {"id": pid, "status": "accepted"}.items() <= next(
        m for m in summary["members"] if m["id"] == pid
    ).items()

    ok(client.post(f"/api/v1/assertions/{aid}/decision", json={"action": "reject"}))
    listed = ok(client.get("/api/v1/publications", params={"person": pid, "limit": 200}))["data"]
    assert pmid not in {p["pmid"] for p in listed}
    assert person_doc(client, pid)["counts"]["accepted"] == before["accepted"]
    assert record["pmid"] == pmid


def test_batch_decisions_and_bulk_undo(setup: Setup) -> None:
    client = setup.client
    pid = person_id(client, setup.world.insiders[3].family)
    queue = ok(client.get("/api/v1/review", params={"person": pid, "limit": 3}))["data"]
    ids = [i["assertion"]["id"] for i in queue]
    assert len(ids) == 3
    rejected = ok(
        client.post("/api/v1/assertions/decisions", json={"ids": ids, "action": "reject"})
    )["data"]
    assert rejected["decided"] == 3 and not rejected["failed"]
    undone = ok(
        client.post(
            "/api/v1/assertions/decisions", json={"ids": [*ids, "asr_missing"], "action": "undo"}
        )
    )["data"]
    assert undone["decided"] == 3 and undone["failed"][0]["id"] == "asr_missing"
    statuses = {ok(client.get(f"/api/v1/assertions/{a}"))["data"]["status"] for a in ids}
    assert statuses == {"suggested"}
    assert (
        client.post(
            "/api/v1/assertions/decisions", json={"ids": [], "action": "accept"}
        ).status_code
        == 422
    )


def test_decision_log_pages_and_labels(setup: Setup) -> None:
    client = setup.client
    first = ok(client.get("/api/v1/decisions", params={"limit": 2}))
    assert len(first["data"]) == 2 and first["meta"]["next_cursor"]
    after = ok(
        client.get("/api/v1/decisions", params={"limit": 2, "cursor": first["meta"]["next_cursor"]})
    )
    assert first["data"][0]["at"] >= after["data"][0]["at"]
    link = next(
        e
        for e in ok(client.get("/api/v1/decisions", params={"limit": 50}))["data"]
        if e["kind"] == "link"
    )
    assert link["subject"]["label"] and link["object"]["label"] and link["assertion_id"]


def test_decisions_survive_resolve_and_rebuild(setup: Setup) -> None:
    client = setup.client
    pid = person_id(client, setup.world.insiders[4].family)
    queue = ok(client.get("/api/v1/review", params={"person": pid, "limit": 2}))["data"]
    accept, reject = (i["assertion"]["id"] for i in queue)
    ok(client.post(f"/api/v1/assertions/{accept}/decision", json={"action": "accept"}))
    ok(client.post(f"/api/v1/assertions/{reject}/decision", json={"action": "reject"}))
    in_place = _summaries(setup.home)
    engine = make_engine(setup.config.ledger_url)
    try:
        build_search(setup.config, setup.home, engine)
        rebuilt = _summaries(setup.home)
        resolve(setup.config, setup.home, engine, today=TODAY)
    finally:
        engine.dispose()
    # In-place updates and a full rebuild agree on every member list and count.
    for ref, summary in rebuilt.items():
        if ref.startswith(("pub:", "grt:", "stu:")):
            key = {"pub:": "members", "grt:": "investigators", "stu:": "officials"}[ref[:4]]
            assert in_place[ref][key] == summary[key], ref
        elif ref.startswith("per_") and ref in in_place:
            assert in_place[ref]["counts"] == summary["counts"], ref
    assert {r for r in rebuilt if not r.startswith("candidate:")} == {
        r for r in in_place if not r.startswith("candidate:")
    }
    # Human decisions are locked: a second resolve leaves them alone.
    assert ok(client.get(f"/api/v1/assertions/{accept}"))["data"]["status"] == "accepted"
    assert ok(client.get(f"/api/v1/assertions/{reject}"))["data"]["status"] == "rejected"


def _summaries(home: DataHome) -> dict[str, dict]:  # type: ignore[type-arg]
    con = sqlite3.connect(index_path(home))
    try:
        return {ref: json.loads(s) for ref, s in con.execute("SELECT ref, summary FROM doc")}
    finally:
        con.close()


def test_update_without_an_index_is_a_no_op(tmp_path: Path) -> None:
    (tmp_path / "tessera.toml").write_text(example_config_text())
    config = load_config(tmp_path / "tessera.toml", env={})
    home = DataHome(config.data.home)
    home.create()
    engine = make_engine(config.ledger_url)
    migrate.upgrade(engine)
    try:
        assert update.apply(config, home, engine, people=["per_x"], records=["pub:1"]) is None
    finally:
        engine.dispose()


# --- identifiers ---------------------------------------------------------------------------------


def test_identifier_decisions(setup: Setup) -> None:
    client = setup.client
    holder = next(
        r for r in setup.world.insiders if r.profile_id is not None and r.orcid and r.papers
    )
    items = ok(client.get("/api/v1/people", params={"q": holder.family}))["data"]
    pid = next(i["id"] for i in items if i["name"].endswith(holder.family))
    path = f"/api/v1/people/{pid}/identifiers"

    profile = ok(client.get(f"/api/v1/people/{pid}"))["data"]
    [suggested] = [i for i in profile["identifiers"] if i["scheme"] == "orcid"]
    assert (suggested["value"], suggested["status"], suggested["source"]) == (
        holder.orcid,
        "suggested",
        "papers",
    )
    assert suggested["papers"] >= 2 and suggested["url"].endswith(holder.orcid)
    assert suggested["record"]["status"] == "public"  # the synthetic lake has the record

    body = {"scheme": "orcid", "value": holder.orcid, "action": "accept", "note": "Checked"}
    result = ok(client.post(path, json=body))["data"]
    assert (result["previous"], result["status"]) == ("suggested", "accepted")
    assert any(i["status"] == "accepted" for i in result["identifiers"])

    # Mistakes are refused with a reason; a malformed iD is a 400, a wrong state a 409.
    bad = client.post(
        path, json={"scheme": "orcid", "value": "0000-0003-9000-0015", "action": "add"}
    )
    assert bad.status_code == 400 and "not a valid ORCID iD" in bad.json()["detail"]
    again = client.post(path, json={"scheme": "orcid", "value": holder.orcid, "action": "accept"})
    assert again.status_code == 409
    assert (
        client.post(
            "/api/v1/people/per_missing/identifiers", json={**body, "action": "add"}
        ).status_code
        == 404
    )

    # Reject, then remove: both logged, newest first, with what changed.
    ok(client.post(path, json={**body, "action": "reject", "note": None}))
    ok(client.post(path, json={**body, "action": "remove", "note": None}))
    log = ok(client.get("/api/v1/decisions", params={"limit": 3}))["data"]
    assert [(e["action"], e["identifier"]["action"]) for e in log] == [
        ("identifier", "remove"),
        ("identifier", "reject"),
        ("identifier", "accept"),
    ]
    assert log[2]["note"] == "Checked" and log[0]["to_status"] is None

    # Identifier decisions never disturb undo of promote and dismiss.
    profile = ok(client.get(f"/api/v1/people/{pid}"))["data"]
    assert not [i for i in profile["identifiers"] if i["scheme"] == "orcid"]


def test_an_id_held_by_two_people_is_flagged(setup: Setup) -> None:
    client = setup.client
    first, second = [r for r in setup.world.insiders if r.profile_id is not None][-2:]
    ids = []
    for r in (first, second):
        items = ok(client.get("/api/v1/people", params={"q": r.family}))["data"]
        ids.append(next(i["id"] for i in items if i["name"].endswith(r.family)))
    shared = "0000-0003-9000-0049"
    for pid in ids:
        ok(
            client.post(
                f"/api/v1/people/{pid}/identifiers",
                json={"scheme": "orcid", "value": shared, "action": "add"},
            )
        )
    profile = ok(client.get(f"/api/v1/people/{ids[0]}"))["data"]
    [held] = [i for i in profile["identifiers"] if i["value"] == shared]
    assert held["also"] == [{"id": ids[1], "name": held["also"][0]["name"], "status": "accepted"}]
    for pid in ids:  # leave the shared world as it was
        ok(
            client.post(
                f"/api/v1/people/{pid}/identifiers",
                json={"scheme": "orcid", "value": shared, "action": "remove"},
            )
        )
