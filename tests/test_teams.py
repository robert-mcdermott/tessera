"""Teams (INT-10): people for an opportunity, the topics they cover and miss, who could fill
the gaps (with reasons, never a ranking of people), and a brief of confirmed work."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tessera.api.app import create_app
from tessera.config import load_config
from tessera.demo import build_demo
from tessera.ledger.db import make_engine
from tessera.ledger.tables import assertion

TODAY = date(2026, 11, 7)


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[object, TestClient]]:
    path = tmp_path_factory.mktemp("teams-demo")
    result = build_demo(path, today=TODAY)
    config = load_config(Path(result["config"]), env={})
    with TestClient(create_app(config), base_url="http://localhost") as client:
        yield config, client


def ok(response):  # type: ignore[no-untyped-def]
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_a_team_for_an_opportunity_shows_what_is_covered_and_missing(demo) -> None:  # type: ignore[no-untyped-def]
    _, client = demo
    team = ok(client.post("/api/v1/teams", json={"opportunity": "9900101"}))
    assert team["opportunity"] == "opp:9900101" and team["name"].startswith("Team for ")
    names = [c["descriptor"] for c in team["concepts"]]
    assert "Receptors, Chimeric Antigen" in names  # the opportunity's reading comes with it
    assert team["people_who_fit"] and team["coverage"]["covered"] == 0
    lead = team["people_who_fit"][0]
    team = ok(client.post(f"/api/v1/teams/{team['id']}/members",
                          json={"person_id": lead["id"], "role": "lead"}))  # fmt: skip
    cov = team["coverage"]
    assert cov["covered"] >= 1 and cov["covered"] + len(cov["missing"]) == len(team["concepts"])
    covered = [c for c in cov["concepts"] if c["covered_by"]]
    hit = covered[0]["covered_by"][lead["id"]]
    assert hit["weight"] >= cov["threshold"] and hit["descriptor"]
    # Narrow the needs to two topics: one the lead covers, one they do not.
    uncovered = [c for c in cov["concepts"] if not c["covered_by"]]
    gap = uncovered[0] if uncovered else {"descriptor_ui": "D009369", "descriptor": "Neoplasms"}
    needs = [{k: covered[0][k] for k in ("descriptor_ui", "descriptor")},
             {k: gap[k] for k in ("descriptor_ui", "descriptor")}]  # fmt: skip
    team = ok(client.post(f"/api/v1/teams/{team['id']}", json={"concepts": needs}))
    assert [c["descriptor_ui"] for c in team["concepts"]] == [c["descriptor_ui"] for c in needs]
    for m in team["coverage"]["missing"]:
        for s in m["suggestions"]:
            assert s["id"] != lead["id"] and s["matches"] and s["name"]
    second = team["people_who_fit"][0]
    team = ok(client.post(f"/api/v1/teams/{team['id']}/members", json={"person_id": second["id"]}))
    assert {m["id"] for m in team["members"]} == {lead["id"], second["id"]}
    assert len(team["coverage"]["together"]) + len(team["coverage"]["new_pairs"]) == 1
    assert team["coverage"]["groups"]


def test_members_and_mistakes(demo) -> None:  # type: ignore[no-untyped-def]
    _, client = demo
    team = ok(client.post("/api/v1/teams", json={"name": "Seed team", "purpose": "A pilot"}))
    assert team["concepts"] == [] and team["opportunity"] is None
    bad = client.post(f"/api/v1/teams/{team['id']}/members",
                      json={"person_id": "per_nobody"})  # fmt: skip
    assert bad.status_code == 400
    people = client.get("/api/v1/people", params={"limit": 1}).json()["data"]
    role = client.post(f"/api/v1/teams/{team['id']}/members",
                       json={"person_id": people[0]["id"], "role": "boss"})  # fmt: skip
    assert role.status_code == 400
    ok(client.post(f"/api/v1/teams/{team['id']}/members", json={"person_id": people[0]["id"]}))
    left = ok(client.delete(f"/api/v1/teams/{team['id']}/members/{people[0]['id']}"))
    assert left["members"] == []
    assert client.post("/api/v1/teams", json={"name": ""}).status_code == 400
    assert client.post("/api/v1/teams", json={"opportunity": "123"}).status_code == 404
    assert client.get("/api/v1/teams/tem_missing").status_code == 404
    assert team["id"] in {t["id"] for t in ok(client.get("/api/v1/teams"))}
    assert ok(client.delete(f"/api/v1/teams/{team['id']}"))["deleted"]


def test_the_brief_lists_only_confirmed_work(demo) -> None:  # type: ignore[no-untyped-def]
    config, client = demo
    team = ok(client.post("/api/v1/teams", json={"opportunity": "9900103"}))
    for person in team["people_who_fit"][:3]:
        ok(client.post(f"/api/v1/teams/{team['id']}/members", json={"person_id": person["id"]}))
    brief = ok(client.get(f"/api/v1/teams/{team['id']}/brief"))
    assert brief["opportunity"]["id"] == "opp:9900103" and len(brief["members"]) == 3
    engine = make_engine(config.ledger_url)
    try:
        with engine.connect() as conn:
            query = select(assertion.c.subject_id, assertion.c.object_id).where(
                assertion.c.predicate == "authored", assertion.c.status == "accepted"
            )
            confirmed = {(s, o.removeprefix("pub:")) for s, o in conn.execute(query)}
    finally:
        engine.dispose()
    for m in brief["members"]:
        assert all((m["id"], p["pmid"]) in confirmed for p in m["papers"])
    assert any(c["covered_by"] for c in brief["coverage"])
    text = client.get(f"/api/v1/teams/{team['id']}/brief", params={"format": "markdown"})
    assert text.status_code == 200 and text.headers["content-type"].startswith("text/markdown")
    assert "## Members" in text.text and "## Coverage" in text.text
    sheet = client.get(f"/api/v1/teams/{team['id']}/brief", params={"format": "csv"})
    assert sheet.text.splitlines()[0] == "name,role,covers,papers,active_grants,studies"
    assert len(sheet.text.splitlines()) == 4
