"""Grant-to-impact threads (INT-08): what followed a grant, typed and sourced, never causal;
clinical citations from iCite and the citing articles' records, kept apart from the
organization's papers; and Insight's impact figures."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from tessera.api.app import create_app
from tessera.config import load_config
from tessera.connectors.base import IngestOptions, Payload
from tessera.connectors.icite import ICiteConnector
from tessera.connectors.pubmed import PubMedConnector
from tessera.demo import build_demo
from tessera.home import DataHome
from tessera.impact import clinical_kind, grant_thread, paper_thread
from tessera.jobs.ingest import INGEST_ORDER, citing_pmids_missing
from tessera.jobs.sync import SECOND
from tessera.lake import Lake
from tessera.ledger.db import make_engine
from tessera.ledger.tables import assertion

from .fakes import EFETCH_XML

TODAY = date(2026, 11, 7)


# --- pure pieces ------------------------------------------------------------------------------


def test_icite_keeps_the_clinical_articles_citing_a_paper() -> None:
    data = {"data": [
        {"pmid": 5, "year": 2020, "cited_by_clin": [30000002, 30000001, 30000002]},
        {"pmid": 6, "year": 2021, "cited_by_clin": None},
    ]}  # fmt: skip
    payload = Payload("pubs", json.dumps(data).encode(), "json", datetime.now(UTC))
    five, six = ICiteConnector.parse(payload)["icite"]
    assert five["clinical_citations"] == [{"citing_pmid": "30000001"}, {"citing_pmid": "30000002"}]
    assert six["clinical_citations"] == []


def test_citing_articles_go_to_their_own_dataset() -> None:
    plain = PubMedConnector.parse(Payload("efetch", EFETCH_XML, "xml", datetime.now(UTC)))
    citing = PubMedConnector.parse(Payload("efetch_citing", EFETCH_XML, "xml", datetime.now(UTC)))
    assert set(plain) == {"pubmed"} and set(citing) == {"pubmed_citing"}
    first = citing["pubmed_citing"][0]
    assert set(first) == {"pmid", "title", "journal", "journal_abbrev", "pub_year", "pub_date",
                          "doi", "publication_types", "first_author", "author_count",
                          "is_retracted"}  # fmt: skip
    assert first["pmid"] == plain["pubmed"][0]["pmid"]
    assert first["author_count"] == len(plain["pubmed"][0]["authors"])


def test_citing_mode_plans_from_given_pmids() -> None:
    from tessera.config import Config

    connector = PubMedConnector.__new__(PubMedConnector)
    connector.config = Config()
    plan = connector.plan(IngestOptions(mode="citing", ids=["3", "1", "3"]), None, TODAY)
    assert plan.mode == "citing" and plan.params["pmids"] == ["3", "1"]
    assert connector.plan(IngestOptions(mode="citing"), None, TODAY).empty


def test_citing_articles_are_fetched_after_icite() -> None:
    for order in (INGEST_ORDER, SECOND):
        assert order.index("pubmed:citing") == order.index("icite") + 1


def test_clinical_kinds() -> None:
    assert clinical_kind(["Practice Guideline", "Journal Article"]) == "guideline"
    assert clinical_kind(["Clinical Trial, Phase II", "Journal Article"]) == "trial"
    assert clinical_kind(["Randomized Controlled Trial"]) == "trial"
    assert clinical_kind(["Meta-Analysis", "Review"]) == "meta_analysis"
    assert clinical_kind(["Review"]) == "review"
    assert clinical_kind(["Case Reports"]) == "clinical"
    assert clinical_kind(None) == "clinical"


# --- on the demo organization ----------------------------------------------------------------


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[object, TestClient]]:
    path = tmp_path_factory.mktemp("impact-demo")
    result = build_demo(path, today=TODAY)
    config = load_config(Path(result["config"]), env={})
    with TestClient(create_app(config), base_url="http://localhost") as client:
        yield config, client


def _program(client: TestClient) -> str:
    grants = client.get("/api/v1/grants", params={"activity": "P01", "limit": 5}).json()["data"]
    return grants[0]["core_project_num"]


def test_the_program_project_reaches_a_guideline_through_two_papers(demo) -> None:  # type: ignore[no-untyped-def]
    _, client = demo
    core = _program(client)
    thread = client.get(f"/api/v1/grants/{core}/thread").json()["data"]
    assert thread["grant"]["core_project_num"] == core
    papers = {p["pmid"]: p for p in thread["papers"]}
    guidelines = [c for c in thread["clinical"] if c["kind"] == "guideline"]
    assert any(len(c["cites"]) >= 2 and set(c["cites"]) <= set(papers) for c in guidelines)
    for c in thread["clinical"]:  # every clinical article cites a paper in this thread
        assert c["cites"] and all(pmid in papers for pmid in c["cites"])
        assert c["known"] and c["year"] and c["kind_label"]
    s = thread["summary"]
    assert s["papers"] == len(thread["papers"]) and s["clinical"] == len(thread["clinical"])
    assert s["guidelines"] == len(guidelines)
    assert s["papers_cited_clinically"] == sum(1 for p in thread["papers"] if p["clinical"])
    assert all("NIH RePORTER" in p["sources"] or "PubMed" in p["sources"] for p in thread["papers"])
    assert any("not causation" in n for n in thread["notes"])
    assert [x["source"] for x in thread["sources"]] == ["ctgov", "icite", "pubmed", "reporter"]


def test_a_thread_reaches_studies_by_reference_and_by_grant(demo) -> None:  # type: ignore[no-untyped-def]
    config, client = demo
    lake = Lake(config.data.home / "lake")
    try:
        cores = [r["c"] for r in lake.rows(
            "SELECT DISTINCT core_project_num AS c FROM ctgov_secondary_id "
            "WHERE core_project_num IS NOT NULL")]  # fmt: skip
    finally:
        lake.close()
    thread = client.get(f"/api/v1/grants/{cores[0]}/thread").json()["data"]
    funded = [s for s in thread["studies"] if any(v["type"] == "funded_by" for v in s["via"])]
    assert funded and all(s["nct_id"].startswith("NCT") for s in funded)
    for s in thread["studies"]:
        for v in s["via"]:
            if v["type"] == "references":
                paper = next(p for p in thread["papers"] if p["pmid"] == v["pmid"])
                assert s["nct_id"] in paper["studies"]


def test_a_rejected_acknowledgment_never_appears_in_a_thread(demo) -> None:  # type: ignore[no-untyped-def]
    config, client = demo
    core = _program(client)
    engine = make_engine(config.ledger_url)
    lake = Lake(config.data.home / "lake")
    try:
        before = grant_thread(engine, lake, core, today=TODAY)
        pmid = before["papers"][0]["pmid"]
        ref = (assertion.c.subject_id == f"pub:{pmid}") & (assertion.c.object_id == f"grt:{core}")
        with engine.begin() as conn:
            status = conn.execute(select(assertion.c.status).where(ref)).scalar_one()
            conn.execute(update(assertion).where(ref).values(status="rejected"))
        try:
            after = grant_thread(engine, lake, core, today=TODAY)
            assert pmid not in {p["pmid"] for p in after["papers"]}
            assert after["summary"]["papers"] == before["summary"]["papers"] - 1
        finally:
            with engine.begin() as conn:
                conn.execute(update(assertion).where(ref).values(status=status))
    finally:
        lake.close()


def test_citing_articles_never_become_the_organizations_papers(demo) -> None:  # type: ignore[no-untyped-def]
    config, _ = demo
    lake = Lake(config.data.home / "lake")
    try:
        citing = {r["pmid"] for r in lake.rows("SELECT pmid FROM citing_article")}
        ours = {r["pmid"] for r in lake.rows("SELECT pmid FROM pubmed_article")}
        rows = lake.rows("SELECT citing_pmid FROM icite_clinical_citation")
        cited = {r["citing_pmid"] for r in rows}
    finally:
        lake.close()
    assert citing and not citing & ours
    assert cited <= citing | ours  # every one has a record, so nothing is left to fetch
    assert citing_pmids_missing(DataHome(config.data.home)) == []
    engine = make_engine(config.ledger_url)
    with engine.connect() as conn:
        linked = set(conn.execute(
            select(assertion.c.object_id).where(assertion.c.predicate == "authored")
        ).scalars())  # fmt: skip
    assert not {f"pub:{p}" for p in citing} & linked


def test_paper_and_person_threads(demo) -> None:  # type: ignore[no-untyped-def]
    config, client = demo
    core = _program(client)
    grant = client.get(f"/api/v1/grants/{core}/thread").json()["data"]
    paper = next(p for p in grant["papers"] if p["clinical"])
    out = client.get(f"/api/v1/publications/{paper['pmid']}/thread").json()["data"]
    assert core in {g["core_project_num"] for g in out["grants"]}
    assert {c["pmid"] for c in out["clinical"]} == set(paper["clinical"])
    assert client.get("/api/v1/publications/1/thread").status_code == 404
    assert client.get("/api/v1/grants/R01XX000000/thread").status_code == 404
    engine = make_engine(config.ledger_url)
    with engine.connect() as conn:
        pi = conn.execute(select(assertion.c.subject_id).where(
            assertion.c.predicate == "investigator_on", assertion.c.object_id == f"grt:{core}",
            assertion.c.status == "accepted")).scalars().first()  # fmt: skip
    mine = client.get(f"/api/v1/people/{pi}/impact").json()["data"]
    assert core in {g["core_project_num"] for g in mine["grants"]}
    assert mine["papers"]["papers"] >= mine["papers"]["papers_cited_clinically"]
    assert client.get("/api/v1/people/per_nobody/impact").status_code == 404
    lake = Lake(config.data.home / "lake")
    try:
        assert paper_thread(engine, lake, "1", today=TODAY) is None
    finally:
        lake.close()


def test_impact_figures_count_each_paper_once(demo) -> None:  # type: ignore[no-untyped-def]
    _, client = demo
    params = {"start": 2022, "end": 2026, "records": "true"}
    clinical = client.get("/api/v1/insight/figures/impact.clinical_citations", params=params)
    assert clinical.status_code == 200, clinical.text
    f = clinical.json()["data"]
    assert f["metric"]["ref"] == "impact.clinical_citations/v1"
    count = client.get("/api/v1/insight/figures/publications.count", params=params).json()["data"]
    assert f["totals"]["papers"] == count["totals"]["papers"]
    assert 0 < f["totals"]["guideline"] <= f["totals"]["cited"] <= f["totals"]["papers"]
    assert sum(r["cited"] for r in f["rows"]) == f["totals"]["cited"]
    cited = [r for r in f["records"] if r["clinical_articles"]]
    assert len(cited) == f["totals"]["cited"]
    assert any("association, not causation" in c for c in f["metric"]["caveats"])
    studies = client.get("/api/v1/insight/figures/impact.studies", params=params).json()["data"]
    assert 0 < studies["totals"]["referenced"] <= studies["totals"]["papers"]
    assert all(
        r["studies"]
        for r in studies["records"]
        if r["pmid"] in {x["pmid"] for x in studies["records"] if x["studies"]}
    )


def test_impact_figures_snapshot_and_verify(demo) -> None:  # type: ignore[no-untyped-def]
    _, client = demo
    figures = [{"metric": "impact.clinical_citations", "options": {}},
               {"metric": "impact.studies", "options": {}}]  # fmt: skip
    report = client.post("/api/v1/insight/reports", json={
        "name": "Impact check", "cohort": "organization", "start": 2022, "end": 2025,
        "figures": figures})  # fmt: skip
    assert report.status_code in (200, 201), report.text
    rid = report.json()["data"]["id"]
    snap = client.post(f"/api/v1/insight/reports/{rid}/snapshots", json={})
    assert snap.status_code in (200, 201), snap.text
    sid = snap.json()["data"]["id"]
    verified = client.get(f"/api/v1/insight/snapshots/{sid}/verify").json()["data"]
    assert verified["same"] and len(verified["figures"]) == 2, verified
