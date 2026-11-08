"""Concept profiles, similar people, and the expert finder (DIS-04 to DIS-06)."""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from tessera.analytics.concepts import _sources, build_profiles, profile_path, similarity_path
from tessera.api.app import create_app
from tessera.config import Config, example_config_text, load_config
from tessera.evidence import store
from tessera.evidence.people import import_roster
from tessera.evidence.resolve import resolve
from tessera.home import DataHome
from tessera.jobs.build import build_search
from tessera.ledger import migrate
from tessera.ledger.db import make_engine

from . import synthetic_org

TODAY = date(2026, 11, 7)


@dataclass
class Org:
    config: Config
    home: DataHome
    engine: Engine
    client: TestClient
    world: synthetic_org.World
    topic_of: dict[str, str]  # person id -> field
    decided: set[str]  # people a curator has worked through
    stand_in: tuple[str, str]  # (person id, descriptor) known only from an author keyword
    built: dict


def _half(person: str) -> bool:
    return int(hashlib.sha256(person.encode()).hexdigest(), 16) % 2 == 0


@pytest.fixture(scope="module")
def org(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Org]:
    tmp = tmp_path_factory.mktemp("discovery")
    (tmp / "tessera.toml").write_text(example_config_text())
    config = load_config(tmp / "tessera.toml", env={})
    home = DataHome(config.data.home)
    home.create()
    engine = make_engine(config.ledger_url)
    migrate.upgrade(engine)
    world = synthetic_org.build(tmp)

    # One new paper has no MeSH yet; its author keywords name a descriptor from another field.
    first = world.insiders[0]
    other_field = next(t for t in synthetic_org.TOPICS if t != first.topic)
    stand_in = synthetic_org.TOPICS[other_field][0]
    unindexed = first.papers[-1]
    record = next(a for a in world.pubmed if a["pmid"] == unindexed)
    record["mesh"] = []
    record["keywords"] = [stand_in.lower(), "not a heading"]

    synthetic_org.load(world, home.lake)
    import_roster(engine, world.roster_path)
    resolve(config, home, engine, today=TODAY)
    people = store.load_people(engine)
    key_of, topic_of = {}, {}
    for r in world.insiders:
        [match] = [p for p in people if any(n.family == r.family for n in p.names)]
        key_of[match.id] = r.key
        topic_of[match.id] = r.topic
    first_id = next(pid for pid, key in key_of.items() if key == first.key)

    # A curator works through half the people, accepting what is true.
    decided = set()
    for row in store.assertions_for(engine, predicate="authored", statuses=("suggested",)):
        person = row["subject_id"]
        pmid = row["object_id"].removeprefix("pub:")
        if person == first_id and pmid == unindexed:
            store.decide(engine, row["id"], "accept", "Curator")
            continue
        if person not in key_of or not _half(person):
            continue
        truth = (key_of[person], pmid) in world.truth
        store.decide(engine, row["id"], "accept" if truth else "reject", "Curator")
        decided.add(person)
    resolve(config, home, engine, today=TODAY)
    build_search(config, home, engine)
    built = build_profiles(config, home, engine, today=TODAY)
    with TestClient(create_app(config), base_url="http://localhost") as client:
        yield Org(
            config,
            home,
            engine,
            client,
            world,
            topic_of,
            decided,
            (first_id, stand_in),
            built,
        )
    engine.dispose()


def profiles(org: Org) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for row in pq.read_table(profile_path(org.home)).to_pylist():
        out[row["person_id"]].append(row)
    return out


def ok(response):  # type: ignore[no-untyped-def]
    assert response.status_code == 200, response.text
    return response.json()


# --- profiles ----------------------------------------------------------------------------------


def test_profiles_are_about_each_persons_field(org: Org) -> None:
    by_person = profiles(org)
    assert org.built["people"] == len(by_person) >= 30
    for person, rows in by_person.items():
        names = [r["descriptor"] for r in sorted(rows, key=lambda r: -r["weight"])]
        assert "Humans" not in names  # a check tag says nothing about expertise
        field = synthetic_org.TOPICS[org.topic_of[person]]
        assert set(names[:3]) <= set(field), (person, names[:3])
        # Unit length: similarity is a plain dot product.
        assert math.fsum(r["weight"] ** 2 for r in rows) == pytest.approx(1.0, abs=1e-6)


def test_strong_suggestions_count_before_curation_catches_up(org: Org) -> None:
    by_person = profiles(org)
    waiting = [p for p in by_person if p not in org.decided and p != org.stand_in[0]]
    assert waiting, "every profiled person was decided"
    for person in waiting:
        assert all(r["confirmed"] == 0 for r in by_person[person])
    for person in org.decided & set(by_person):
        assert any(r["confirmed"] > 0 for r in by_person[person])


def test_likely_weight_zero_counts_confirmed_papers_only(org: Org) -> None:
    strict = replace(org.config, discovery=replace(org.config.discovery, likely_weight=0.0))
    papers, _ = _sources(org.engine, strict)
    assert papers.num_rows > 0 and all(papers.column("confirmed").to_pylist())
    loose, _ = _sources(org.engine, org.config)
    assert loose.num_rows > papers.num_rows
    assert set(loose.column("factor").to_pylist()) == {1.0, org.config.discovery.likely_weight}


def test_author_keywords_stand_in_for_missing_mesh(org: Org) -> None:
    person, descriptor = org.stand_in
    [row] = [r for r in profiles(org)[person] if r["descriptor"] == descriptor]
    assert row["provisional"] and row["papers"] == 1 and row["confirmed"] == 1
    # Concepts with real MeSH behind them are not provisional.
    assert not any(r["provisional"] for r in profiles(org)[person] if r["descriptor"] != descriptor)


def test_recent_work_counts_more(org: Org, tmp_path: Path) -> None:
    # Ten years on, every paper has aged two half-lives: raw weights shrink, shapes stay.
    later = DataHome(tmp_path / "later")
    later.create()
    later.lake.rmdir()
    later.lake.symlink_to(org.home.lake, target_is_directory=True)
    build_profiles(org.config, later, org.engine, today=date(TODAY.year + 10, 1, 1))
    now = {
        (r["person_id"], r["descriptor_ui"]): r
        for r in pq.read_table(profile_path(org.home)).to_pylist()
    }
    then = pq.read_table(profile_path(later)).to_pylist()
    assert then
    for row in then:
        before = now[(row["person_id"], row["descriptor_ui"])]
        assert row["raw"] < before["raw"] / 3


# --- similar people ----------------------------------------------------------------------------


def test_similar_people_are_symmetric_explained_and_mostly_in_the_same_field(org: Org) -> None:
    rows = pq.read_table(similarity_path(org.home)).to_pylist()
    assert rows and org.built["pairs"] * 2 == len(rows)
    score = {(r["person_id"], r["other_id"]): r["score"] for r in rows}
    for (a, b), s in score.items():
        assert score[(b, a)] == pytest.approx(s)
        assert s >= org.config.discovery.similarity_floor
    for r in rows:
        assert 0 < len(r["shared"]) <= 5
        parts = [c["contribution"] for c in r["shared"]]
        assert parts == sorted(parts, reverse=True)
    same = sum(org.topic_of[r["person_id"]] == org.topic_of[r["other_id"]] for r in rows)
    assert same / len(rows) >= 0.9


def test_similar_people_endpoint(org: Org) -> None:
    person = next(iter(profiles(org)))
    body = ok(org.client.get(f"/api/v1/people/{person}/similar"))
    assert body["data"], "nobody is similar"
    first = body["data"][0]
    assert {"id", "name", "units", "score", "shared"} <= set(first)
    assert first["name"] != first["id"]  # names come from the index
    scores = [p["score"] for p in body["data"]]
    assert scores == sorted(scores, reverse=True)
    assert org.client.get("/api/v1/people/per_nobody/similar").status_code == 404


def test_what_drives_a_profile(org: Org) -> None:
    person = next(iter(profiles(org)))
    body = ok(org.client.get(f"/api/v1/people/{person}/concepts"))
    assert body["meta"]["built"] is True
    rows = body["data"]
    assert 0 < len(rows) <= 30
    assert {"descriptor_ui", "descriptor", "weight", "papers", "confirmed", "provisional"} <= set(
        rows[0]
    )
    assert [r["weight"] for r in rows] == sorted((r["weight"] for r in rows), reverse=True)
    assert org.client.get("/api/v1/people/per_nobody/concepts").status_code == 404


# --- the expert finder -------------------------------------------------------------------------


def test_finding_experts_in_words(org: Org) -> None:
    body = ok(org.client.get("/api/v1/experts", params={"q": "Immunologic Memory"}))
    meta = body["meta"]
    assert meta["ready"] is True and meta["note"] is None
    # Named exactly, a concept is taken as meant; the papers found add their neighbours.
    assert meta["concepts"][0]["descriptor"] == "Immunologic Memory"
    assert len(meta["concepts"]) > 1
    people = body["data"]
    assert len(people) >= 3
    assert all(org.topic_of[p["id"]] == "immunology" for p in people[:3])
    assert all(p["matches"] and p["name"] for p in people)
    scores = [p["score"] for p in people]
    assert scores == sorted(scores, reverse=True)


def test_finding_experts_by_concept(org: Org) -> None:
    found = ok(org.client.get("/api/v1/concepts", params={"q": "immuno"}))["data"]
    [chosen] = [c for c in found if c["descriptor"] == "Immunotherapy"]
    assert chosen["people"] > 0
    for concept in (chosen["descriptor_ui"], f"msh:{chosen['descriptor_ui']}"):
        body = ok(org.client.get("/api/v1/experts", params={"concept": concept}))
        assert [c["descriptor"] for c in body["meta"]["concepts"]] == ["Immunotherapy"]
        assert body["data"] and len(body["data"]) == chosen["people"]
        for p in body["data"]:
            assert [m["descriptor"] for m in p["matches"]] == ["Immunotherapy"]


def test_words_that_match_nothing(org: Org) -> None:
    body = ok(org.client.get("/api/v1/experts", params={"q": "qqqzzz wvwvwv"}))
    assert body["data"] == [] and body["meta"]["concepts"] == [] and body["meta"]["ready"]
    assert ok(org.client.get("/api/v1/concepts", params={"q": " "}))["data"] == []


def test_before_profiles_are_built(client: TestClient) -> None:
    body = ok(client.get("/api/v1/experts", params={"q": "anything"}))
    assert body["data"] == [] and body["meta"]["ready"] is False
    assert "tessera build profiles" in body["meta"]["note"]
    assert ok(client.get("/api/v1/concepts", params={"q": "cancer"}))["data"] == []
