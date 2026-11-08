"""The demo organization: invented, deterministic, and complete enough for every screen."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tessera.api.app import create_app
from tessera.config import load_config
from tessera.demo import DemoError, build_demo
from tessera.demo.world import build_world

TODAY = date(2026, 11, 7)


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[dict, TestClient]]:
    path = tmp_path_factory.mktemp("demo")
    result = build_demo(path, today=TODAY)
    config = load_config(Path(result["config"]), env={})
    with TestClient(create_app(config), base_url="http://localhost") as client:
        yield result, client


def test_identifiers_can_never_be_real_ones() -> None:
    world = build_world(today=TODAY)
    assert all(int(r["pmid"]) >= 900_000_000 for r in world.pubmed)
    assert all(r["doi"].startswith("10.5555/") for r in world.pubmed)
    assert all(s["nct_id"].startswith("NCT9") for s in world.studies)
    assert all(int(r["core_project_num"][-6:]) >= 990_000 for r in world.reporter)
    orcids = {p.orcid for p in world.people if p.orcid}
    assert orcids and all(o.startswith("0000-0000-") for o in orcids)
    assert all(p.email.endswith("@corvane.example") for p in world.people if p.email)
    headings = {m["descriptor_ui"] for r in world.pubmed for m in r["mesh"]}
    assert all(re.fullmatch(r"D9\d{5}", ui) for ui in headings)


def test_the_same_seed_builds_the_same_organization() -> None:
    one, two = build_world(seed=3, today=TODAY), build_world(seed=3, today=TODAY)
    assert [r["title"] for r in one.pubmed] == [r["title"] for r in two.pubmed]
    assert [p.name for p in one.people] == [p.name for p in two.people]
    assert [r["title"] for r in build_world(seed=4, today=TODAY).pubmed] != [
        r["title"] for r in one.pubmed
    ]


def test_the_demo_fills_every_screen(demo) -> None:  # type: ignore[no-untyped-def]
    result, client = demo
    assert result["people"] >= 80 and result["papers"] >= 500
    assert result["accepted"] > 500 and result["rejected"] >= 20 and result["merged"] >= 10

    def data(path: str, **params):  # type: ignore[no-untyped-def]
        response = client.get(path, params=params)
        assert response.status_code == 200, (path, response.text)
        return response.json()

    sources = data("/api/v1/sources")["data"]["sources"]
    assert all(not s["stale"] for s in sources if s["name"] != "icite" or s["enabled"])
    assert data("/api/v1/review")["meta"]["total"] > 0  # something left to review
    assert data("/api/v1/duplicates")["data"]  # pairs left for a curator
    assert data("/api/v1/candidates")["data"]  # discovered people waiting
    assert data("/api/v1/evidence")["data"]["evaluation"]["decisions"]["negatives"] >= 20
    experts = data("/api/v1/experts", q="memory T cells")
    assert experts["meta"]["ready"] and experts["data"]
    network = data("/api/v1/graph/network")
    assert network["meta"]["groups"] and len(network["data"]["nodes"]) >= 50
    assert len(data("/api/v1/graph/units")["data"]["units"]) >= 4
    assert data("/api/v1/graph/missed", min_similarity=0.1)["meta"]["total"] > 0
    assert len(data("/api/v1/accounts")["data"]) >= 4


def test_an_existing_demo_is_kept_unless_forced(demo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    build_demo(tmp_path, today=TODAY, labs_per_area=1)
    with pytest.raises(DemoError, match="exists"):
        build_demo(tmp_path, today=TODAY, labs_per_area=1)
    build_demo(tmp_path, today=TODAY, labs_per_area=1, force=True)
