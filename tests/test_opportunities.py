"""Funding opportunities (wave 5): Grants.gov's, fetched by change (INT-09), and those an
organization tracks itself (INT-12), searchable like every other record."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tessera.api.app import create_app
from tessera.config import load_config
from tessera.connectors.grantsgov import (
    gg_date,
    nih_details,
    opportunity_status,
    parse_opportunity,
    plain_text,
)
from tessera.connectors.http import HttpClient
from tessera.demo import build_demo
from tessera.jobs.ingest import ingest, opportunities_held
from tessera.lake import Lake
from tessera.ledger.db import make_engine
from tessera.ledger.tables import opportunity
from tessera.opportunities import catalog, store
from tessera.opportunities.store import OpportunityError

from .conftest import Instance
from .fakes import FakeApi, gg_details, gg_hit, grants_gov_api, no_sleep

TODAY = date(2026, 11, 7)


# --- reading Grants.gov ----------------------------------------------------------------------


def test_dates_text_and_nih_titles() -> None:
    assert gg_date("May 21, 2026 12:00:00 AM EDT") == date(2026, 5, 21)
    assert gg_date("05/21/2026") == date(2026, 5, 21)
    assert gg_date("") is None and gg_date("soon") is None
    assert plain_text("<p>A &amp; B</p><ul><li>one</li></ul>") == "A & B\n\n- one"
    assert plain_text("  ") is None
    out = nih_details("RFA-CA-27-019", "Informatics (U01 Clinical Trial Optional)")
    guide = "https://grants.nih.gov/grants/guide/rfa-files/RFA-CA-27-019.html"
    assert out == {"activity_codes": ["U01"], "clinical_trial": "optional", "guide_url": guide}
    two = nih_details("PAR-25-1", "Bridges (K99/R00 Independent Clinical Trial Not Allowed)")
    assert two["activity_codes"] == ["K99", "R00"] and two["clinical_trial"] == "not_allowed"
    assert two["guide_url"].endswith("/pa-files/PAR-25-1.html")
    assert nih_details("NSF 26-1", "Molecular Foundations")["activity_codes"] == []


def test_synopsis_and_forecast_records() -> None:
    posted = parse_opportunity(gg_details(gg_hit("990001", "RFA-CA-27-001", "CAR T (R01)"))["data"],
                               {"listing": "x", "listed_status": "posted"})  # fmt: skip
    assert posted is not None
    assert (posted["doc_type"], posted["closes"], posted["archives"]) == (
        "synopsis",
        date(2026, 12, 1),
        date(2027, 3, 15),
    )
    assert posted["award_ceiling"] == 500_000 and posted["award_floor"] is None
    assert posted["applicant_types"] == ["06", "20"] and posted["categories"] == ["Health"]
    assert posted["description"].startswith("Research on CAR T cells.")
    assert posted["parent_agency"] == "Department of Health and Human Services"
    assert "example.gov" not in str(posted)  # contact details are not kept
    hit = gg_hit("990002", "PA-27-002", "Memory T cells (R01)", status="forecasted")
    forecast = parse_opportunity(gg_details(hit)["data"], {"listed_status": "forecasted"})
    assert forecast is not None and forecast["doc_type"] == "forecast"
    assert forecast["expected_post"] == date(2026, 12, 1)
    assert opportunity_status(forecast, TODAY) == "forecasted"
    assert opportunity_status(forecast, date(2028, 1, 1)) == "forecasted"  # dates are estimates
    assert opportunity_status(posted, TODAY) == "posted"
    assert opportunity_status(posted, date(2027, 1, 1)) == "closed"
    assert opportunity_status(posted, date(2027, 4, 1)) == "archived"
    assert opportunity_status({**posted, "listed_status": "unlisted"}, TODAY) == "closed"


@pytest.fixture
def opportunities_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[FakeApi, list]]:
    hits = [gg_hit("990001", "RFA-CA-27-001", "Cell therapy (R01 Clinical Trial Optional)"),
            gg_hit("990002", "PA-27-002", "Memory T cells (R01)", status="forecasted"),
            gg_hit("990003", "26-501", "Membrane fusion", agency="NSF")]  # fmt: skip
    api = grants_gov_api(hits)
    original = HttpClient.__init__

    def init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", api.transport())
        kwargs.setdefault("sleep", no_sleep)
        original(self, *args, **kwargs)

    monkeypatch.setattr(HttpClient, "__init__", init)
    yield api, hits


def test_a_sync_fetches_only_what_changed(instance: Instance, opportunities_api) -> None:  # type: ignore[no-untyped-def]
    api, hits = opportunities_api
    engine = make_engine(instance.config.ledger_url)
    try:
        first = ingest(instance.config, instance.home, engine, "grants_gov", today=TODAY)
        assert first.status == "succeeded" and len(api.calls("fetchOpportunity")) == 3
        again = ingest(instance.config, instance.home, engine, "grants_gov", today=TODAY)
        assert again.status == "succeeded" and len(api.calls("fetchOpportunity")) == 3
        assert any("unchanged" in n for n in again.report.notes)
        hits[0] = {**hits[0], "closeDate": "12/15/2026"}  # the deadline moved
        del hits[2]  # no longer listed: it closed
        third = ingest(instance.config, instance.home, engine, "grants_gov", today=TODAY)
        fetched = [str(r.content) for r in api.calls("fetchOpportunity")[3:]]
        assert third.status == "succeeded" and len(fetched) == 2
        held = opportunities_held(instance.home, TODAY)
        assert held["990003"]["open"] is False and held["990001"]["open"] is True
        with Lake(instance.home.lake) as lake:
            rows = {
                r["opportunity_id"]: r for r in lake.rows("SELECT * FROM grants_gov_opportunity")
            }
        assert rows["990001"]["closes"] == date(2026, 12, 15)
        assert rows["990003"]["listed_status"] == "unlisted"
        assert (instance.home.raw / "grants_gov" / third.run_id / "index.jsonl").is_file()
    finally:
        engine.dispose()


def test_categories_and_agencies_narrow_a_sync(
    config_file: Path, instance, opportunities_api
) -> None:  # type: ignore[no-untyped-def]
    api, _ = opportunities_api
    text = config_file.read_text().replace(
        "categories = []\nagencies = []", 'categories = ["HL"]\nagencies = ["HHS-NIH"]'
    )
    assert text != config_file.read_text()
    config_file.write_text(text)
    config = load_config(config_file, env={})
    engine = make_engine(config.ledger_url)
    try:
        result = ingest(config, instance.home, engine, "grants_gov", today=TODAY)
    finally:
        engine.dispose()
    assert "categories HL" in result.plan.description and "from HHS-NIH" in result.plan.description
    sent = api.calls("search2")[0]
    assert b'"fundingCategories":"HL"' in sent.content.replace(b" ", b"")
    assert len(api.calls("fetchOpportunity")) == 2  # NSF's is left out


def test_bad_grants_gov_settings_are_refused(config_file: Path) -> None:
    from tessera.config import ConfigError

    bad = 'categories = ["health"]\nagencies = ["nih "]'
    config_file.write_text(config_file.read_text().replace("categories = []\nagencies = []", bad))
    with pytest.raises(ConfigError, match="funding category"):
        load_config(config_file, env={})


# --- the organization's own -------------------------------------------------------------------


def test_local_opportunities_are_checked_and_kept(instance: Instance) -> None:
    engine = make_engine(instance.config.ledger_url)
    try:
        oid = store.create(engine, {"source": "Example Foundation", "title": "Seed grants",
                                    "closes_on": "2026-12-01", "award_ceiling": "$50,000",
                                    "applicant_types": "6; 20", "limited_submission": "yes"},
                           "Tester")  # fmt: skip
        with Lake(instance.home.lake) as lake:
            view = catalog.get(engine, lake, f"opp:{oid}", TODAY)
        assert view is not None and view["status"] == "posted" and view["source"] == "local"
        assert view["award_ceiling"] == 50_000 and view["applicant_types"] == ["06", "20"]
        assert view["limited_submission"] is True and view["sponsor"] == "Example Foundation"
        for bad, message in (({"title": ""}, "needs a title"),
                             ({"closes_on": "soon"}, "not a date"),
                             ({"applicant_types": "77"}, "not Grants.gov"),
                             ({"url": "ftp://x"}, "http"), ({"kind": "lottery"}, "kind is one of"),
                             ({"award_floor": 9, "award_ceiling": 1}, "more than")):  # fmt: skip
            with pytest.raises(OpportunityError, match=message):
                store.change(engine, oid, bad, "Tester")
        store.archive(engine, oid, "Tester")
        with Lake(instance.home.lake) as lake:
            assert catalog.get(engine, lake, oid, TODAY)["status"] == "archived"  # type: ignore[index]
        store.change(engine, oid, {"archived": False}, "Tester")
        with Lake(instance.home.lake) as lake:
            assert catalog.get(engine, lake, oid, date(2027, 1, 1))["status"] == "closed"  # type: ignore[index]
    finally:
        engine.dispose()


def test_imports_update_in_place_and_write_nothing_on_a_problem(instance: Instance) -> None:
    engine = make_engine(instance.config.ledger_url)
    rows = [{"external_id": "a", "title": "Pilot awards", "kind": "internal"},
            {"external_id": "b", "title": "Travel awards", "kind": "internal"}]  # fmt: skip
    count = lambda: engine.connect().execute(select(func.count()).select_from(opportunity)).scalar()  # noqa: E731
    try:
        dry = store.import_rows(engine, rows, "Tester", source="Office of Research", dry_run=True)
        assert (dry["created"], count()) == (2, 0)
        done = store.import_rows(engine, rows, "Tester", source="Office of Research")
        assert done["created"] == 2 and count() == 2
        rows[0]["closes_on"] = "2027-02-01"
        again = store.import_rows(engine, rows[:1], "Tester", source="Office of Research",
                                  complete=True)  # fmt: skip
        assert (again["updated"], again["unchanged"], again["archived"]) == (1, 0, 1)
        bad = store.import_rows(engine, [{"title": "No source"}], "Tester")
        assert bad["problems"] and count() == 2
        twice = store.import_rows(engine, rows + rows, "Tester", source="Office of Research")
        assert any("listed twice" in p for p in twice["problems"])
    finally:
        engine.dispose()


# --- on the demo organization ----------------------------------------------------------------


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[object, TestClient]]:
    path = tmp_path_factory.mktemp("opportunities-demo")
    result = build_demo(path, today=TODAY)
    config = load_config(Path(result["config"]), env={})
    with TestClient(create_app(config), base_url="http://localhost") as client:
        yield config, client


def test_the_demo_lists_federal_and_local_opportunities(demo) -> None:  # type: ignore[no-untyped-def]
    _, client = demo
    out = client.get("/api/v1/opportunities", params={"status": ["posted", "forecasted"],
                                                      "limit": 50}).json()  # fmt: skip
    data = out["data"]
    assert out["meta"]["total"] == len(data) == 14
    sources = {f["value"]: f["count"] for f in out["meta"]["facets"]["source"]}
    assert sources == {"grants_gov": 11, "local": 3}
    closes = [o["closes"] for o in data if o["status"] == "posted"]
    assert closes == sorted(closes)  # soonest first
    small = next(o for o in data if o["number"] == "PA-26-998")
    assert small["activity_codes"] == ["R43", "R44"]
    closed = client.get("/api/v1/opportunities", params={"status": "closed"}).json()["data"]
    assert [o["number"] for o in closed] == ["RFA-AI-26-990"]
    found = client.get("/api/v1/search", params={"q": "germline HIV vaccine"}).json()["data"]
    assert found["opportunity"]["total"] >= 1


def test_an_opportunity_and_the_organizations_own(demo) -> None:  # type: ignore[no-untyped-def]
    _, client = demo
    federal = client.get("/api/v1/opportunities/9900101").json()["data"]
    assert federal["source"] == "grants_gov" and federal["clinical_trial"] == "optional"
    assert federal["sources"][0]["source"] == "grants_gov"
    assert client.get("/api/v1/opportunities/123").status_code == 404
    assert client.post("/api/v1/opportunities/9900101", json={"title": "x"}).status_code == 409
    made = client.post("/api/v1/opportunities", json={
        "source": "Example Foundation", "title": "Bridge funding for sabbaticals",
        "closes_on": "2027-01-15"}).json()["data"]  # fmt: skip
    assert made["id"].startswith("opp:opl_")
    found = client.get("/api/v1/opportunities", params={"q": "sabbaticals"}).json()
    assert [o["id"] for o in found["data"]] == [made["id"]]
    changed = client.post(f"/api/v1/opportunities/{made['id']}", json={"award_ceiling": 40000})
    assert changed.json()["data"]["award_ceiling"] == 40_000
    archived = client.delete(f"/api/v1/opportunities/{made['id']}").json()["data"]
    assert archived["status"] == "archived"
    sheet = "source,title,closes_on\nExample Trust,Equipment awards,2027-03-01\n"
    imported = client.post("/api/v1/opportunities/import", json={"csv": sheet}).json()["data"]
    assert imported["created"] == 1 and not imported["problems"]
    found = client.get("/api/v1/opportunities", params={"q": "equipment"}).json()
    assert found["meta"]["total"] == 1
    assert client.post("/api/v1/opportunities/import", json={}).status_code == 400


def test_opportunities_can_be_saved_searches(demo) -> None:  # type: ignore[no-untyped-def]
    _, client = demo
    saved = client.post("/api/v1/saved-searches", json={
        "name": "Cell therapy calls", "kind": "opportunity", "q": "CAR",
        "filters": {"status": ["posted"]}})  # fmt: skip
    assert saved.status_code in (200, 201), saved.text
    assert saved.json()["data"]["total"] >= 1
