"""The 1.0 promise (PLT-17): every route is marked stable or experimental, no change breaks a
client of a stable route, and a retiring route says so.

The contract is ``tests/api_contract.json``. When this test fails, either the change breaks
clients (undo it, or add beside it and deprecate the old way) or the contract must grow:
``UPDATE_CONTRACT=1 uv run pytest tests/test_api_contract.py`` records what was added, and never
removes or changes what is there; taking something out is an edit by hand, for a major version
or a route past its sunset.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tessera.api import stability
from tessera.api.app import create_app
from tessera.config import Config, load_config
from tessera.demo import build_demo
from tessera.ledger.db import make_engine
from tessera.ledger.tables import cv_document, report, report_snapshot, saved_search, team

from . import contract

CONTRACT = Path(__file__).parent / "api_contract.json"
UPDATE = os.environ.get("UPDATE_CONTRACT") == "1"
TODAY = date(2026, 11, 7)
LOCAL = "http://127.0.0.1"

# Parts of answers that are free-form by design, recorded but not their contents: a run's
# tallies (each job's own), a change's details, and an Insight figure's rows, totals, and
# options, whose columns each metric's definition and version describe.
FREE_FORM = {
    "GET /api/v1/runs": ("data[].counts",),
    "GET /api/v1/groups/{group_ref}/history": ("data[].detail",),
    "GET /api/v1/people/{person_id}/profile/history": ("data[].before", "data[].after"),
    "GET /api/v1/insight/figures/{metric_id}": ("data.rows[]", "data.totals", "data.options"),
    "GET /api/v1/insight/reports": ("data[].figures[].options",),
    "GET /api/v1/insight/reports/{report_id}": ("data.figures[].options",),
    "GET /api/v1/insight/snapshots/{snapshot_id}": (
        "data.figures[].rows[]",
        "data.figures[].totals",
        "data.figures[].options",
        "data.manifest.inputs",
    ),
}

# Stable GET routes the contract records only by their parameters, with why.
NOT_CALLED = {
    "GET /api/v1/auth/sso/start": "a browser's redirect to the provider, with single sign-on on",
    "GET /api/v1/auth/sso/callback": "the provider's redirect back",
}


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Config, TestClient]]:
    path = tmp_path_factory.mktemp("contract-demo")
    result = build_demo(path, today=TODAY)
    config = load_config(Path(result["config"]), env={})
    with TestClient(create_app(config), base_url=LOCAL) as client:
        yield config, client


def _samples(config: Config, client: TestClient) -> dict[str, str]:
    """A value for every path parameter, from the demo organization."""
    engine = make_engine(config.ledger_url)
    try:
        with engine.connect() as conn:
            cv = conn.execute(select(cv_document.c.id, cv_document.c.person_id).limit(1)).one()
            values = {
                "doc_id": cv.id,
                "person_id": cv.person_id,
                "team_id": conn.execute(select(team.c.id).limit(1)).scalar_one(),
                "report_id": conn.execute(select(report.c.id).limit(1)).scalar_one(),
                "snapshot_id": conn.execute(select(report_snapshot.c.id).limit(1)).scalar_one(),
                "search_id": conn.execute(select(saved_search.c.id).limit(1)).scalar_one(),
            }
    finally:
        engine.dispose()

    def first(url: str, key: str, **params: Any) -> str:
        items = client.get(url, params={"limit": 5, **params}).json()["data"]
        return str(next(i[key] for i in items if i.get(key)))

    papers = client.get(f"/api/v1/people/{values['person_id']}/publications").json()["data"]
    values["pmid"] = str(papers[0]["publication"]["pmid"])
    values["assertion_id"] = str(next(p["assertion"]["id"] for p in papers if p.get("assertion")))
    values["core_project_num"] = first("/api/v1/grants", "core_project_num")
    values["nct_id"] = first("/api/v1/studies", "nct_id")
    values["opportunity_id"] = first("/api/v1/opportunities", "id")
    values["group_ref"] = first("/api/v1/groups", "slug")
    values["metric_id"] = "publications.count"
    return values


def _record(config: Config, client: TestClient) -> dict[str, Any]:
    spec = client.get("/api/openapi.json").json()
    samples = _samples(config, client)
    recorded: dict[str, Any] = {}
    for key, operation in contract.stable_operations(spec).items():
        method, path = key.split(" ", 1)
        entry: dict[str, Any] = {
            "query": contract.parameters(spec, operation),
            "body": contract.body(spec, operation),
            "response": None,
        }
        if method == "GET" and key not in NOT_CALLED:
            url = path.format(**samples)
            answer = client.get(url)
            assert answer.status_code == 200, f"{key}: {url} answered {answer.status_code}"
            media = answer.headers.get("content-type", "").split(";")[0]
            entry["response"] = {"media": media}
            if media.endswith("json"):
                found = contract.shape(answer.json())
                free = FREE_FORM.get(key, ())
                if free:
                    entry["response"]["free_form"] = list(free)
                found = {
                    p: k
                    for p, k in found.items()
                    if not any(p.startswith((f + ".", f + "[")) for f in free)
                }
                entry["response"]["shape"] = {p: sorted(k) for p, k in sorted(found.items())}
        recorded[key] = entry
    return recorded


def test_every_route_is_stable_or_experimental(demo: tuple[Config, TestClient]) -> None:
    _, client = demo
    spec = client.get("/api/openapi.json").json()
    levels = {
        f"{m.upper()} {p}": o.get("x-stability")
        for p, item in spec["paths"].items()
        for m, o in item.items()
        if m in contract.METHODS and p.startswith("/api/v1/")
    }
    assert levels and set(levels.values()) <= {stability.STABLE, stability.EXPERIMENTAL}
    assert levels["GET /api/v1/people/{person_id}"] == stability.STABLE
    assert levels["GET /api/v1/graph/network"] == stability.EXPERIMENTAL


def test_no_change_breaks_a_client_of_a_stable_route(demo: tuple[Config, TestClient]) -> None:
    config, client = demo
    now = _record(config, client)
    was = json.loads(CONTRACT.read_text("utf-8"))["operations"] if CONTRACT.is_file() else {}
    problems = contract.compare(was, now)
    if UPDATE or not CONTRACT.is_file():
        merged = contract.merge(was, now)
        CONTRACT.write_text(
            json.dumps({"about": ABOUT, "operations": merged}, indent=1, ensure_ascii=False) + "\n",
            "utf-8",
        )
    added = sorted(set(now) - set(was))
    assert not problems, "Changes that break clients of stable routes:\n" + "\n".join(problems)
    assert not added or UPDATE, (
        f"New stable routes, not yet in the contract: {', '.join(added)}. Record them with "
        "UPDATE_CONTRACT=1 uv run pytest tests/test_api_contract.py"
    )


ABOUT = (
    "What each stable route of Tessera's API accepts and answers with, kept by "
    "tests/test_api_contract.py. Recording it again adds and never removes; see that test."
)


def test_the_comparison_finds_what_breaks_and_lets_additions_through() -> None:
    was = {
        "GET /api/v1/things": {
            "query": {"q": {"type": "string", "required": False}},
            "body": None,
            "response": {
                "media": "application/json",
                "shape": {"data": ["array"], "data[].id": ["string"], "data[].n": ["number"]},
            },
        },
        "POST /api/v1/things": {
            "query": {},
            "body": {
                "media": "application/json",
                "fields": {"name": {"type": "string", "required": True}},
            },
            "response": None,
        },
    }
    same = json.loads(json.dumps(was))
    assert contract.compare(was, same) == []
    added = json.loads(json.dumps(was))
    added["GET /api/v1/things"]["query"]["sort"] = {"type": "string", "required": False}
    added["GET /api/v1/things"]["response"]["shape"]["data[].title"] = ["string"]
    added["POST /api/v1/things"]["body"]["fields"]["note"] = {"type": "string", "required": False}
    added["GET /api/v1/more"] = was["GET /api/v1/things"]
    assert contract.compare(was, added) == []

    broken = json.loads(json.dumps(was))
    del broken["GET /api/v1/things"]["response"]["shape"]["data[].n"]  # a field removed
    broken["GET /api/v1/things"]["query"] = {"query": {"type": "string", "required": False}}
    broken["POST /api/v1/things"]["body"]["fields"]["name"]["type"] = "integer"  # a type changed
    problems = contract.compare(was, broken)
    assert any("'data[].n' is gone" in p for p in problems)
    assert any("parameter 'q' is gone" in p for p in problems)  # renamed
    assert any("'name' was string, now integer" in p for p in problems)
    retyped = json.loads(json.dumps(was))
    retyped["GET /api/v1/things"]["response"]["shape"]["data[].id"] = ["number"]
    assert contract.compare(was, retyped) == [
        "GET /api/v1/things: field 'data[].id' was string, now number"
    ]
    # Recording again keeps what was there, so a change is never blessed by re-recording.
    assert contract.compare(contract.merge(was, retyped), retyped)


def test_a_deprecated_route_says_so(
    demo: tuple[Config, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _ = demo
    gone = stability.Deprecation(
        announced=date(2027, 1, 4), sunset=date(2027, 7, 5), replacement="/api/v1/stats"
    )
    monkeypatch.setitem(stability.DEPRECATED, ("GET", "/api/v1/meta"), gone)
    with TestClient(create_app(config), base_url=LOCAL) as client:
        answer = client.get("/api/v1/meta")
        assert answer.status_code == 200
        assert answer.headers["Deprecation"] == "@1799020800"
        assert answer.headers["Sunset"] == "Mon, 05 Jul 2027 00:00:00 GMT"
        assert answer.headers["Link"] == '</api/v1/stats>; rel="successor-version"'
        assert "Deprecation" not in client.get("/api/v1/stats").headers
        operation = client.get("/api/openapi.json").json()["paths"]["/api/v1/meta"]["get"]
        assert operation["deprecated"] is True and operation["x-sunset"] == "2027-07-05"
