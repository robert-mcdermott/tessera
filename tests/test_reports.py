"""Saved searches (DIS-10), reports and snapshots that regenerate exactly (INT-03), and exports
that carry their filters, definitions, and provenance (INT-04)."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from tessera.api.app import create_app
from tessera.config import load_config
from tessera.core.clock import utc_now
from tessera.demo import build_demo
from tessera.ledger.db import make_engine
from tessera.ledger.tables import assertion, saved_search

TODAY = date(2026, 11, 7)
PROGRAM = "group:cancer-immunotherapy-program"


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[object, TestClient]]:
    path = tmp_path_factory.mktemp("reports-demo")
    result = build_demo(path, today=TODAY)
    config = load_config(Path(result["config"]), env={})
    with TestClient(create_app(config), base_url="http://localhost") as client:
        yield config, client


def ok(response) -> dict:  # type: ignore[no-untyped-def]
    assert response.status_code == 200, response.text
    return response.json()["data"]


# --- saved searches ----------------------------------------------------------------------------


def test_saved_searches_show_what_is_new_since_last_seen(demo) -> None:  # type: ignore[no-untyped-def]
    config, client = demo
    made = ok(client.post("/api/v1/saved-searches", json={
        "name": "Recent lymphoma", "kind": "publication", "q": "lymphoma",
        "filters": {"year": ["2025", "2026"]},
    }))  # fmt: skip
    assert made["total"] > 2 and made["new"] == 0  # what it finds now counts as seen
    engine = make_engine(config.ledger_url)
    try:
        with engine.begin() as conn:  # as if two of the matches had arrived since
            seen = conn.execute(select(saved_search.c.seen)).scalar()
            conn.execute(update(saved_search).values(seen=seen[2:]))
    finally:
        engine.dispose()
    listed = ok(client.get("/api/v1/saved-searches"))
    assert [(s["name"], s["new"]) for s in listed] == [("Recent lymphoma", 2)]
    detail = ok(client.get(f"/api/v1/saved-searches/{made['id']}"))
    assert len(detail["new_refs"]) == 2 and all(r.startswith("pub:") for r in detail["new_refs"])
    assert all(i["summary"]["title"] for i in detail["new_items"])
    ok(client.post(f"/api/v1/saved-searches/{made['id']}/seen"))
    assert ok(client.get("/api/v1/saved-searches"))[0]["new"] == 0
    assert ok(client.post(f"/api/v1/saved-searches/{made['id']}", json={"name": "Lymphoma"}))[
        "name"] == "Lymphoma"  # fmt: skip
    everything = ok(client.post("/api/v1/saved-searches", json={"name": "T cells", "q": "T cells"}))
    assert everything["kind"] == "everything" and everything["total"] > 0
    bad = client.post("/api/v1/saved-searches", json={"name": "x", "kind": "publication",
                                                       "filters": {"colour": ["red"]}})  # fmt: skip
    assert bad.status_code == 400 and "no filter" in bad.json()["detail"]
    for s in ok(client.get("/api/v1/saved-searches")):
        assert client.delete(f"/api/v1/saved-searches/{s['id']}").status_code == 200
    assert client.get(f"/api/v1/saved-searches/{made['id']}").status_code == 404


# --- reports and snapshots ---------------------------------------------------------------------

FIGURES = [
    {"metric": "publications.count"},
    {"metric": "funding.awarded", "options": {"amount": "direct"}},
    {"metric": "topics.landscape", "options": {"level": "area"}},
    {"metric": "studies.by_phase"},
    {"metric": "funding.ending", "options": {"months": "24"}},
]


def make_report(client: TestClient, name: str = "Program review") -> dict:
    return ok(client.post("/api/v1/insight/reports", json={
        "name": name, "description": "For the annual review", "cohort": PROGRAM,
        "start": 2022, "end": 2026, "figures": FIGURES,
    }))  # fmt: skip


def test_reports_are_checked_and_listed(demo) -> None:  # type: ignore[no-untyped-def]
    _, client = demo
    report = make_report(client, "Checked")
    assert [f["metric"] for f in report["figures"]] == [f["metric"] for f in FIGURES]
    assert report["figures"][1]["options"] == {"amount": "direct", "awardee": "organization"}
    cases = [
        ({"figures": [{"metric": "people.ranked"}]}, "no metric"),
        ({"figures": [{"metric": "funding.awarded", "options": {"amount": "all"}}]}, "not one of"),
        ({"cohort": "group:nobody"}, "No group"),
        ({"start": 2026, "end": 2020}, "starts after it ends"),
        ({"name": "Checked"}, "already a report"),
    ]
    for change, message in cases:
        body = {"name": "Another", "cohort": PROGRAM, "figures": FIGURES, **change}
        response = client.post("/api/v1/insight/reports", json=body)
        assert response.status_code in (400, 404) and message in response.json()["detail"], change
    change = {"name": "Renamed", "end": None}
    renamed = ok(client.post(f"/api/v1/insight/reports/{report['id']}", json=change))
    assert (renamed["name"], renamed["start"], renamed["end"]) == ("Renamed", 2022, None)
    assert "Renamed" in {r["name"] for r in ok(client.get("/api/v1/insight/reports"))}
    assert client.delete(f"/api/v1/insight/reports/{report['id']}").status_code == 200


def test_a_snapshot_keeps_its_numbers_after_the_data_changes_and_verifies(demo) -> None:  # type: ignore[no-untyped-def]
    config, client = demo
    report = make_report(client)
    snap = ok(client.post(f"/api/v1/insight/reports/{report['id']}/snapshots",
                          json={"note": "Before the sync"}))  # fmt: skip
    assert snap["summary"]["figures"][0] == "publications.count/v1"
    frozen = ok(client.get(f"/api/v1/insight/snapshots/{snap['id']}"))
    before = frozen["figures"][0]["totals"]["papers"]
    manifest = frozen["manifest"]
    assert manifest["cohort"]["label"] == "Cancer Immunotherapy Program"
    assert {"members", "paper_links", "papers", "awards", "studies"} <= set(manifest["inputs"])
    assert all(len(f["digest"]) == 64 for f in manifest["figures"])

    # Something changes: a curator rejects one of the program's confirmed papers.
    record = next(r for r in ok(client.get(
        "/api/v1/insight/figures/publications.count",
        params={"cohort": PROGRAM, "start": 2022, "end": 2026, "records": "true"}))["records"]
        if r["status"] == "confirmed" and len(r["links"]) == 1)  # fmt: skip
    engine = make_engine(config.ledger_url)
    try:
        with engine.begin() as conn:
            conn.execute(update(assertion).where(assertion.c.id == record["links"][0])
                         .values(status="rejected", decided_at=utc_now()))  # fmt: skip
    finally:
        engine.dispose()
    live = ok(client.get("/api/v1/insight/figures/publications.count",
                         params={"cohort": PROGRAM, "start": 2022, "end": 2026}))  # fmt: skip
    assert live["totals"]["papers"] == before - 1
    again = ok(client.get(f"/api/v1/insight/snapshots/{snap['id']}"))
    assert again["figures"][0]["totals"]["papers"] == before
    verified = ok(client.get(f"/api/v1/insight/snapshots/{snap['id']}/verify"))
    assert verified["same"] and len(verified["figures"]) == len(FIGURES)

    # Tampering with a frozen input is caught.
    path = config.data.home / "snapshots" / snap["id"] / "inputs" / "papers.parquet"
    table = pq.read_table(path)
    pq.write_table(table.slice(1), path)
    tampered = ok(client.get(f"/api/v1/insight/snapshots/{snap['id']}/verify"))
    assert not tampered["same"] and "papers has changed" in tampered["problems"][0]
    pq.write_table(table, path)
    assert ok(client.get(f"/api/v1/insight/snapshots/{snap['id']}/verify"))["same"]

    # Deleting the report keeps its snapshots, which anyone may have cited.
    assert client.delete(f"/api/v1/insight/reports/{report['id']}").status_code == 200
    kept = ok(client.get(f"/api/v1/insight/snapshots/{snap['id']}"))
    assert kept["report_id"] is None and kept["figures"][0]["totals"]["papers"] == before


# --- exports ------------------------------------------------------------------------------------


def test_exports_carry_definitions_filters_and_provenance(demo) -> None:  # type: ignore[no-untyped-def]
    _, client = demo
    params = {"cohort": PROGRAM, "start": 2022, "end": 2026, "amount": "indirect"}
    csv_zip = client.get("/api/v1/insight/figures/funding.awarded/export",
                         params={**params, "format": "csv"})  # fmt: skip
    assert csv_zip.status_code == 200 and csv_zip.headers["content-type"] == "application/zip"
    assert "funding-awarded-csv.zip" in csv_zip.headers["content-disposition"]
    archive = zipfile.ZipFile(io.BytesIO(csv_zip.content))
    assert set(archive.namelist()) == {"funding-awarded.csv", "funding-awarded-records.csv",
                                       "about.json", "README.txt"}  # fmt: skip
    about = json.loads(archive.read("about.json"))
    assert about["metric"]["ref"] == "funding.awarded/v1" and about["metric"]["counting"]
    assert about["options"]["amount"]["label"] == "Indirect costs"
    assert about["export"]["filters"] == {**params, "start": 2022, "end": 2026}
    assert about["sources"][0]["label"] == "NIH RePORTER" and about["sources"][0]["last_success_at"]
    readme = archive.read("README.txt").decode()
    assert "Awards by fiscal year (funding.awarded/v1)" in readme and "Cohort: Cancer" in readme
    header = archive.read("funding-awarded.csv").decode().splitlines()[0]
    assert header.startswith("fiscal_year,amount,new,renewal")
    records = archive.read("funding-awarded-records.csv").decode().splitlines()
    assert records[0].startswith("appl_id,core_project_num") and len(records) > 2

    parquet_zip = client.get("/api/v1/insight/figures/publications.count/export",
                             params={"cohort": PROGRAM, "format": "parquet"})  # fmt: skip
    archive = zipfile.ZipFile(io.BytesIO(parquet_zip.content))
    table = pq.read_table(io.BytesIO(archive.read("publications-count.parquet")))
    meta = json.loads(table.schema.metadata[b"tessera"])
    assert meta["metric"]["ref"] == "publications.count/v1" and meta["cohort"]["size"] > 0
    records = pq.read_table(io.BytesIO(archive.read("publications-count-records.parquet")))
    assert "links" in records.column_names and records.num_rows > 0

    as_json = client.get("/api/v1/insight/figures/studies.by_status/export",
                         params={"format": "json"}).json()  # fmt: skip
    assert set(as_json) >= {"export", "about", "columns", "rows", "records"}
    assert as_json["about"]["metric"]["caveats"]
    assert client.get("/api/v1/insight/figures/studies.by_status/export",
                      params={"format": "xlsx"}).status_code == 400  # fmt: skip


def test_a_snapshot_exports_every_figure_and_its_manifest(demo) -> None:  # type: ignore[no-untyped-def]
    _, client = demo
    report = make_report(client, "Exported")
    snap = ok(client.post(f"/api/v1/insight/reports/{report['id']}/snapshots", json={}))
    response = client.get(f"/api/v1/insight/snapshots/{snap['id']}/export",
                          params={"format": "csv"})  # fmt: skip
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    assert "manifest.json" in names and "README.txt" in names
    assert "01-publications-count/publications-count.csv" in names
    assert "05-funding-ending/about.json" in names
    about = json.loads(archive.read("02-funding-awarded/about.json"))
    assert about["export"]["snapshot"] == snap["id"]
    assert about["export"]["filters"]["amount"] == "direct"
    one = client.get(f"/api/v1/insight/snapshots/{snap['id']}/export",
                     params={"format": "json", "figure": 3}).json()  # fmt: skip
    assert one["about"]["metric"]["ref"] == "topics.landscape/v1"
    assert client.get(f"/api/v1/insight/snapshots/{snap['id']}/export",
                      params={"figure": 9}).status_code == 404  # fmt: skip


def test_the_report_command_freezes_verifies_and_exports(demo, tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    from tessera.cli import main

    config, client = demo
    make_report(client, "From the command line")
    args = ["--config", str(config.path)]
    assert main([*args, "report", "snapshot", "From the command line", "--note", "cli"]) == 0
    snapshot_id = capsys.readouterr().out.strip().split()[-1]
    assert snapshot_id.startswith("snp_")
    assert main([*args, "report", "list"]) == 0
    assert "From the command line" in capsys.readouterr().out
    assert main([*args, "report", "verify", snapshot_id]) == 0
    assert "computes again exactly (5 figures)" in capsys.readouterr().out
    out = tmp_path / "snapshot.zip"
    assert main([*args, "report", "export", snapshot_id, "--format", "parquet", "--out",
                 str(out)]) == 0  # fmt: skip
    assert "manifest.json" in zipfile.ZipFile(out).namelist()
