"""The simulated organization and ``tessera bench`` (PLT-14), at a tiny size."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from tessera.bench import simulate as sim
from tessera.bench.run import Report, build, machine, nightly, pages

TODAY = date(2026, 11, 8)


def test_the_simulation_is_the_same_every_time_and_shaped_like_an_organization() -> None:
    one = sim.simulate(200, 2000, seed=3, today=TODAY)
    two = sim.simulate(200, 2000, seed=3, today=TODAY)
    assert [p.name for p in one.people] == [p.name for p in two.people]
    assert len(one.people) == 200 and len({p.name for p in one.people}) == 200
    families = Counter(p.family for p in one.people)
    assert len(families) > 80  # surnames repeat, but most people have their own
    assert all(p.lab for p in one.people) and one.pis and one.reporter and one.studies
    batches = list(sim.papers(one))
    assert sum(len(b) for b in batches) == 2000
    pmids = [r["pmid"] for b in batches for r in b]
    assert len(set(pmids)) == len(pmids)
    paper = batches[0][0]
    assert paper["authors"] and paper["mesh"] and paper["pub_year"] <= TODAY.year
    insiders = [a for a in paper["authors"] if any("Corvane" in x for x in a["affiliations"])]
    assert insiders or any(not a["affiliations"] for a in paper["authors"])
    assert [r["pmid"] for r in next(iter(sim.papers(two)))[:5]] == pmids[:5]


def test_bench_builds_runs_a_night_and_times_the_app(tmp_path: Path) -> None:
    say: list[str] = []
    report = Report(40, 800, machine())
    build(tmp_path / "org", 40, 800, seed=5, say=say.append, report=report)
    names = [s.name for s in report.build]
    assert names[0].startswith("simulate") and "build the search index" in names
    assert all(s.seconds >= 0 for s in report.build)
    peaks = [s.peak_mb for s in report.build]
    assert all(p is None or p > 0 for p in peaks) and peaks == sorted(peaks, key=lambda p: p or 0)
    nightly(tmp_path / "org", say=say.append, report=report)
    assert report.nightly[0].name.startswith("write 300 new papers")
    assert report.sizes["lake"] > 0 and report.sizes["ledger"] > 0
    state = json.loads((tmp_path / "org" / "bench-state.json").read_text())
    assert state == {"people": 40, "papers": 800, "seed": 5, "nightly": 1}
    pages(tmp_path / "org", samples=2, say=say.append, report=report)
    timed = {p.name: p for p in report.pages}
    assert {"A profile", "Search everything", "Review queue"} <= set(timed)
    assert all(p.samples == 2 and p.first_ms > 0 for p in report.pages)
    assert report.server_memory_mb is None or report.server_memory_mb > 0
    insight = [p for p in report.pages if p.name.startswith("Insight")]
    assert insight and all(p.cold for p in insight)
    assert report.to_json()["pages"][0]["within"] in (True, False)


@pytest.mark.parametrize(
    ("features", "flags"),
    [
        ([], None),
        (["coauthor_overlap"], None),
        (["slot_conflict"], "conflict"),
        (["name_ambiguity"], "ambiguous"),
        (["orcid_rejected", "name_ambiguity"], "conflict ambiguous"),
    ],
)
def test_review_flags_come_from_the_evidence(features: list[str], flags: str | None) -> None:
    from tessera.evidence.store import review_flags

    assert review_flags([{"feature": f, "weight": 1} for f in features]) == flags
    assert review_flags({"not": "a list"}) is None
