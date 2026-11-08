"""The evidence engine on a synthetic organization with known answers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine

from tessera.evidence import store
from tessera.evidence.evaluate import evaluate, render_markdown, write_report
from tessera.evidence.people import import_roster
from tessera.evidence.resolve import resolve
from tessera.lake import Lake, LakeWriter
from tessera.lake.schemas import PUBMED
from tessera.ledger.db import make_engine

from . import synthetic_org
from .conftest import Instance

TODAY = date(2026, 11, 7)


@dataclass
class Scenario:
    instance: Instance
    engine: Engine
    world: synthetic_org.World
    key_of: dict[str, str]  # ledger person ID -> synthetic researcher key

    def resolve(self):  # type: ignore[no-untyped-def]
        return resolve(self.instance.config, self.instance.home, self.engine, today=TODAY)

    def authored(self) -> dict[tuple[str, str], dict]:
        return {
            (self.key_of[r["subject_id"]], r["object_id"].removeprefix("pub:")): r
            for r in store.assertions_for(self.engine, predicate="authored")
            if r["subject_id"] in self.key_of
        }

    def person_id(self, key: str) -> str:
        return next(pid for pid, k in self.key_of.items() if k == key)


def make_scenario(instance: Instance, tmp_path: Path, **build_args) -> Scenario:  # type: ignore[no-untyped-def]
    engine = make_engine(instance.config.ledger_url)
    world = synthetic_org.build(tmp_path, **build_args)
    synthetic_org.load(world, instance.home.lake)
    import_roster(engine, world.roster_path)
    resolve(instance.config, instance.home, engine, today=TODAY)
    people = store.load_people(engine, ("active", "former"))
    key_of = {}
    for researcher in world.insiders:
        [match] = [p for p in people if any(n.family == researcher.family for n in p.names)]
        key_of[match.id] = researcher.key
    return Scenario(instance, engine, world, key_of)


@pytest.fixture
def scenario(instance: Instance, tmp_path: Path):  # type: ignore[no-untyped-def]
    s = make_scenario(instance, tmp_path)
    yield s
    s.engine.dispose()


def metrics(s: Scenario, threshold: float) -> tuple[float, float]:
    predicted = {k for k, r in s.authored().items() if r["score"] >= threshold}
    true_positives = len(predicted & s.world.truth)
    return true_positives / len(predicted), true_positives / len(s.world.truth)


# --- people -----------------------------------------------------------------------------------


def test_people_from_grants_and_roster(scenario: Scenario) -> None:
    active = store.load_people(scenario.engine, ("active",))
    assert len(active) == 40
    from_grants = [p for p in active if p.ids("nih_profile")]
    assert len(from_grants) == 30
    assert all(p.display_name.split()[-1] == p.names[0].family for p in active)
    assert store.load_people(scenario.engine, ("candidate",)) == []  # nobody unexplained
    members = store.assertions_for(scenario.engine, predicate="member_of")
    assert len(members) == 10 and all(m["status"] == "accepted" for m in members)


def test_discovery_proposes_unknown_affiliated_authors(scenario: Scenario) -> None:
    newcomer = {
        "family": "Quill",
        "given": "Zed Q",
        "initials": "ZQ",
        "orcid": None,
        "affiliations": ["Department of Medicine, Example University, Example City."],
    }
    articles = [
        {
            "pmid": str(36_000_000 + i),
            "title": "New work",
            "pub_year": 2026,
            "authors": [{**newcomer, "position": 1, "author_count": 1}],
        }
        for i in range(3)
    ]
    with LakeWriter(scenario.instance.home.lake, PUBMED, "run_newcomer") as writer:
        writer.add(articles)
    result = scenario.resolve()
    assert result.counts["discovery"]["candidates_created"] == 1
    [candidate] = store.load_people(scenario.engine, ("candidate",))
    assert candidate.display_name == "Zed Q Quill"
    assert scenario.resolve().counts["discovery"]["candidates_created"] == 0  # no duplicates


# --- authorship -------------------------------------------------------------------------------


def test_authorship_meets_targets(scenario: Scenario) -> None:
    strong_precision, _ = metrics(scenario, 0.95)
    review_precision, review_recall = metrics(scenario, 0.60)
    retrieved = set(scenario.authored()) & scenario.world.truth
    assert strong_precision >= 0.99
    assert len(retrieved) / len(scenario.world.truth) >= 0.95
    assert review_precision >= 0.90 and review_recall >= 0.95


def test_same_field_namesakes_are_left_for_review(instance: Instance, tmp_path: Path) -> None:
    s = make_scenario(instance, tmp_path, hard=True)
    strong_precision, strong_recall = metrics(s, 0.95)
    review_precision, _ = metrics(s, 0.60)
    s.engine.dispose()
    assert strong_precision >= 0.99 and strong_recall >= 0.95
    assert review_precision >= 0.90


def test_every_assertion_explains_itself(scenario: Scenario) -> None:
    rows = store.assertions_for(scenario.engine, reported_only=False)
    assert rows
    for row in rows:
        assert row["evidence"], row
        assert all(item["explanation"] for item in row["evidence"])
    sample = next(r for r in rows if r["predicate"] == "authored" and r["score"] >= 0.95)
    assert {"prior"} < {e["feature"] for e in sample["evidence"]}
    assert sample["qualifiers"]["author_position"] >= 1


def test_one_author_position_belongs_to_one_person(scenario: Scenario) -> None:
    researcher = scenario.world.insiders[0]
    roster = scenario.world.roster_path.parent / "namesake.csv"
    roster.write_text(f"family,given\n{researcher.family},{researcher.given[0]}xel Q\n")
    import_roster(scenario.engine, roster)
    scenario.resolve()
    strong_slots: dict[tuple[str, int], set[str]] = {}
    for row in store.assertions_for(scenario.engine, predicate="authored"):
        if row["score"] >= 0.95:
            slot = (row["object_id"], row["qualifiers"]["author_position"])
            strong_slots.setdefault(slot, set()).add(row["subject_id"])
    assert all(len(people) == 1 for people in strong_slots.values())


def test_decisions_survive_resolving_again(scenario: Scenario) -> None:
    rows = scenario.authored()
    true_strong = sorted(
        k for k, r in rows.items() if k in scenario.world.truth and r["score"] >= 0.95
    )
    false_weak = sorted(k for k, r in rows.items() if k not in scenario.world.truth)
    decisions = {
        true_strong[0]: ("accept", "accepted"),
        true_strong[1]: ("reject", "rejected"),  # a curator may disagree with the evidence
        true_strong[2]: ("defer", "deferred"),
        false_weak[0]: ("accept", "accepted"),  # ...in either direction
    }
    for pair, (action, _) in decisions.items():
        store.decide(scenario.engine, rows[pair]["id"], action, "Curator")
    scenario.resolve()
    scenario.resolve()
    after = scenario.authored()
    for pair, (_, status) in decisions.items():
        assert after[pair]["status"] == status
        assert after[pair]["id"] == rows[pair]["id"]
    assert after[true_strong[0]]["locked"] and after[true_strong[1]]["locked"]
    store.decide(scenario.engine, rows[true_strong[1]]["id"], "undo", "Curator")
    scenario.resolve()
    assert scenario.authored()[true_strong[1]]["status"] == "suggested"


def test_evaluation_report(scenario: Scenario, tmp_path: Path) -> None:
    rows = scenario.authored()
    for pair, row in list(rows.items())[:60]:
        action = "accept" if pair in scenario.world.truth else "reject"
        store.decide(scenario.engine, row["id"], action, "Curator")
    report = evaluate(scenario.engine, scenario.instance.config)
    assert report["gold"]["examples"] == 60
    assert report["retrieval_recall"] == 1.0
    assert report["at"]["strong"]["precision"] == 1.0
    assert {"common names", "rare names"} >= set(report["slices"]) or report["slices"]
    json_path, md_path = write_report(report, tmp_path / "eval")
    assert json_path.is_file() and "Candidate retrieval recall" in md_path.read_text()
    assert "| Measure | Value | Target |" in render_markdown(report)

    gold = tmp_path / "gold.csv"
    researcher = scenario.world.insiders[0]
    pmid = researcher.papers[0]
    gold.write_text(f"person,pmid,label\n{scenario.person_id(researcher.key)},{pmid},1\n")
    assert evaluate(scenario.engine, scenario.instance.config, gold)["gold"]["examples"] >= 60


# --- links ------------------------------------------------------------------------------------


def test_source_links(scenario: Scenario) -> None:
    engine = scenario.engine
    investigators = store.assertions_for(engine, predicate="investigator_on")
    assert len(investigators) == 30
    assert all(r["qualifiers"]["role"] == "contact_pi" for r in investigators)
    assert investigators[0]["qualifiers"]["fiscal_years"] == [2024, 2025, 2026]
    acks = store.assertions_for(engine, predicate="acknowledges")
    both = [
        a
        for a in acks
        if {e["feature"] for e in a["evidence"]} == {"reporter_link", "pubmed_grant_list"}
    ]
    assert both and all(a["status"] == "accepted" and a["origin"] == "source" for a in acks)
    assert len(store.assertions_for(engine, predicate="references")) == 4
    assert len(store.assertions_for(engine, predicate="funded_by")) == 4


def test_links_a_source_stops_reporting_are_marked(scenario: Scenario) -> None:
    linked = {link["pmid"] for link in scenario.world.links}
    article = next(a for a in scenario.world.pubmed if a["grants"] and a["pmid"] not in linked)
    with LakeWriter(scenario.instance.home.lake, PUBMED, "run_correction") as writer:
        writer.add([{**article, "grants": []}])
    scenario.resolve()
    [ack] = [
        a
        for a in store.assertions_for(
            scenario.engine, predicate="acknowledges", reported_only=False
        )
        if a["subject_id"] == f"pub:{article['pmid']}"
    ]
    assert ack["reported"] is False and ack["status"] == "accepted"


def test_study_officials(scenario: Scenario) -> None:
    rows = store.assertions_for(scenario.engine, predicate="official_of")
    for index, researcher in enumerate(scenario.world.insiders[:4]):
        study = f"stu:NCT0900000{index}"
        best = max((r for r in rows if r["object_id"] == study), key=lambda r: r["score"])
        assert scenario.key_of[best["subject_id"]] == researcher.key
        assert best["score"] >= 0.6


# --- CLI --------------------------------------------------------------------------------------


def test_cli(instance: Instance, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tessera.cli import EXIT_OK, main

    world = synthetic_org.build(tmp_path)
    synthetic_org.load(world, instance.home.lake)
    config = ["--config", str(instance.path)]
    assert main([*config, "people", "import", str(world.roster_path)]) == EXIT_OK
    assert "10 people created" in capsys.readouterr().out
    assert main([*config, "sync", "--no-fetch"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "30 new from grants" in out and "strong" in out and "investigator_on: 30 links" in out
    assert main([*config, "people", "list", "--limit", "5"]) == EXIT_OK
    assert "suggested" in capsys.readouterr().out
    assert main([*config, "eval", "authorship"]) == EXIT_OK
    assert "Authorship evaluation" in capsys.readouterr().out
    with Lake(instance.home.lake) as lake:
        assert lake.scalar("SELECT count(*) FROM pubmed_article") == len(world.pubmed)


def test_co_investigators_need_affiliated_papers(scenario: Scenario) -> None:
    from tessera.lake.schemas import REPORTER

    def project(appl_id: int, profile_id: int, family: str, given: str) -> dict:
        return {
            "appl_id": appl_id,
            "core_project_num": "U01CA999999",
            "fiscal_year": 2026,
            "org_name": "EXAMPLE UNIVERSITY",
            "subproject_id": None,
            "investigators": [
                {
                    "profile_id": 10_000,
                    "family": scenario.world.insiders[0].family,
                    "given": scenario.world.insiders[0].given,
                    "is_contact_pi": True,
                },
                {
                    "profile_id": profile_id,
                    "family": family,
                    "given": given,
                    "is_contact_pi": False,
                },
            ],
        }

    affiliated = {
        "family": "Vale",
        "given": "Iris",
        "initials": "I",
        "orcid": None,
        "affiliations": ["Department of Medicine, Example University."],
    }
    with LakeWriter(scenario.instance.home.lake, REPORTER, "run_multi_pi") as writer:
        writer.add([project(990_001, 777, "Farr", "Otto"), project(990_002, 778, "Vale", "Iris")])
    with LakeWriter(scenario.instance.home.lake, PUBMED, "run_vale") as writer:
        writer.add(
            [
                {
                    "pmid": str(37_000_000 + i),
                    "title": "Joint work",
                    "pub_year": 2026,
                    "authors": [{**affiliated, "position": 1}],
                }
                for i in range(2)
            ]
        )
    result = scenario.resolve()
    assert result.counts["people"]["uncertain"] == 2
    assert result.counts["people"]["promoted"] == 1
    statuses = {
        p.display_name: p.status
        for p in store.load_people(scenario.engine, ("active", "candidate"))
    }
    assert statuses["Otto Farr"] == "candidate"  # a co-PI elsewhere: not presumed a member
    assert statuses["Iris Vale"] == "active"  # her papers carry the affiliation
