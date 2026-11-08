from __future__ import annotations

import gzip
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import insert, select

from tessera.config import example_config_text, load_config
from tessera.connectors.base import IngestOptions
from tessera.connectors.http import HttpClient
from tessera.core.errors import TesseraError
from tessera.home import DataHome
from tessera.jobs import runs
from tessera.jobs.ingest import ingest, ingest_many, linked_pmids_missing, replay
from tessera.jobs.lock import JobLockedError, job_lock
from tessera.lake import Lake
from tessera.lake.manifest import read_manifest
from tessera.lake.schemas import TABLES
from tessera.ledger import migrate
from tessera.ledger.db import make_engine
from tessera.ledger.tables import ingest_run

from .conftest import Instance
from .fakes import (
    EFETCH_XML,
    FakeApi,
    ctgov_api,
    ctgov_study,
    icite_api,
    no_sleep,
    orcid_api,
    orcid_record,
    pubmed_api,
    reporter_api,
    reporter_project,
)

TODAY = date(2026, 11, 7)


def world() -> FakeApi:
    """All four public APIs, with linked data across them."""
    api = FakeApi()
    projects = [reporter_project(i, f"R01CA{i:06d}") for i in range(1, 4)]
    links = {"R01CA000001": ["31000001", "31000002"], "R01CA000002": ["31000004"]}
    studies = [ctgov_study("NCT00000001"), ctgov_study("NCT00000002", pmids=("31000003",))]
    for part in (pubmed_api(), reporter_api(projects, links), ctgov_api(studies), icite_api()):
        api.routes.update(part.routes)
    # NLM's file server: this year's MeSH, and an HTML page (status 200) for next year's.
    sample = gzip.compress(
        (Path(__file__).parent / "fixtures" / "mesh" / "desc-sample.xml").read_bytes()
    )
    api.route(
        ".gz",
        lambda request: (
            httpx.Response(200, content=sample)
            if request.url.path.endswith("desc2026.gz")
            else httpx.Response(200, text="<!doctype html><p>Page not found</p>")
        ),
    )
    return api


@pytest.fixture
def network(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    api = world()
    original = HttpClient.__init__

    def init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", api.transport())
        kwargs.setdefault("sleep", no_sleep)
        original(self, *args, **kwargs)

    monkeypatch.setattr(HttpClient, "__init__", init)
    return api


@pytest.fixture
def engine(instance: Instance):  # type: ignore[no-untyped-def]
    engine = make_engine(instance.config.ledger_url)
    yield engine
    engine.dispose()


def written(result) -> int:  # type: ignore[no-untyped-def]
    return sum(sum(s["written"].values()) for s in result.counts["lake"].values())


def snapshot(home: DataHome) -> dict[str, list[dict]]:
    """Current lake content, ignoring which run wrote it."""
    with Lake(home.lake) as lake:
        out = {}
        for name, spec in TABLES.items():
            keys = ", ".join(f'"{k}"' for k in spec.key)
            rows = lake.rows(f'SELECT * EXCLUDE (_run_id) FROM "{name}" ORDER BY {keys}')
            out[name] = rows
        return out


def test_ingest_all_sources(instance: Instance, engine, network: FakeApi) -> None:  # type: ignore[no-untyped-def]
    results = ingest_many(instance.config, instance.home, engine, today=TODAY)
    assert [(r.source, r.plan.mode, r.status) for r in results] == [
        ("reporter", "org", "succeeded"),
        ("ctgov", "org", "succeeded"),
        ("pubmed", "org", "succeeded"),
        ("orcid", "refresh", "succeeded"),
        ("pubmed", "linked", "succeeded"),
        ("icite", "refresh", "succeeded"),
        ("mesh", "annual", "succeeded"),
    ]
    with Lake(instance.home.lake) as lake:
        count = lambda t: lake.scalar(f'SELECT count(*) FROM "{t}"')  # noqa: E731
        assert count("reporter_project") == 3
        assert count("reporter_investigator") == 3
        assert count("reporter_publication_link") == 3
        assert count("ctgov_study") == 2
        assert count("ctgov_reference") == 2
        assert count("pubmed_article") == 4
        assert count("icite_metrics") == 3  # iCite has nothing for one new paper
        assert count("mesh_descriptor") == 3
        assert (
            lake.scalar("SELECT _source_version FROM ctgov_study LIMIT 1") == "2025-11-06T09:00:00"
        )
    # Linked mode found nothing missing, because the sweep already fetched those papers.
    assert results[4].plan.empty
    # Checkpoints for the organization sweeps, nothing for ID-based runs.
    for result in results[:3]:
        assert runs.get_checkpoint(engine, result.source, result.plan.scope) is not None
    # Raw payloads are kept for every fetch.
    assert (instance.home.raw / "pubmed" / results[2].run_id / "index.jsonl").is_file()
    recorded = {r["id"]: r for r in runs.recent_runs(engine)}
    assert recorded[results[0].run_id]["status"] == "succeeded"
    assert recorded[results[0].run_id]["counts"]["report"]["total_found"] == 3
    assert set(runs.last_success(engine)) == {
        "reporter",
        "ctgov",
        "pubmed",
        "icite",
        "orcid",
        "mesh",
    }


def test_rerun_is_idempotent(instance: Instance, engine, network: FakeApi) -> None:  # type: ignore[no-untyped-def]
    ingest_many(instance.config, instance.home, engine, today=TODAY)
    before = snapshot(instance.home)
    again = ingest_many(instance.config, instance.home, engine, today=TODAY)
    assert all(r.status == "succeeded" for r in again)
    assert sum(written(r) for r in again if r.counts) == 0
    assert snapshot(instance.home) == before


def test_incremental_run_uses_checkpoint(instance: Instance, engine, network: FakeApi) -> None:  # type: ignore[no-untyped-def]
    first = ingest(instance.config, instance.home, engine, "ctgov", today=TODAY)
    network.requests.clear()
    later = ingest(instance.config, instance.home, engine, "ctgov", today=date(2026, 11, 20))
    assert later.plan.params["advanced"] == "AREA[LastUpdatePostDate]RANGE[2026-11-06,2026-11-20]"
    assert runs.get_checkpoint(engine, "ctgov", first.plan.scope) == "2026-11-20"


def test_partial_run_does_not_advance_checkpoint(
    instance: Instance, engine, network: FakeApi
) -> None:  # type: ignore[no-untyped-def]
    network.route("efetch.fcgi", lambda r: httpx.Response(400, text="history server unhappy"))
    result = ingest(instance.config, instance.home, engine, "pubmed", today=TODAY)
    assert result.status == "partial"
    assert len(result.report.failures) == 1
    assert runs.get_checkpoint(engine, "pubmed", result.plan.scope) is None


def test_failed_search_is_recorded(instance: Instance, engine, network: FakeApi) -> None:  # type: ignore[no-untyped-def]
    network.route("esearch.fcgi", lambda r: httpx.Response(400, text="Invalid query"))
    result = ingest(instance.config, instance.home, engine, "pubmed", today=TODAY)
    assert result.status == "failed" and "Invalid query" in (result.error or "")
    assert runs.get_run(engine, result.run_id)["status"] == "failed"


def test_replay_rebuilds_identical_lake(
    instance: Instance, engine, network: FakeApi, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    results = ingest_many(instance.config, instance.home, engine, today=TODAY)
    original = snapshot(instance.home)

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    (other_dir / "tessera.toml").write_text(example_config_text())
    other_config = load_config(other_dir / "tessera.toml", env={})
    other = DataHome(other_config.data.home)
    other.create()
    shutil.copytree(instance.home.raw, other.raw, dirs_exist_ok=True)
    other_engine = make_engine(other_config.ledger_url)
    migrate.upgrade(other_engine)
    network.requests.clear()
    for result in results:
        if result.plan and not result.plan.empty:
            replay(other_config, other, other_engine, result.source, result.run_id)
    other_engine.dispose()
    assert network.requests == []  # replay never touches the network
    assert snapshot(other) == original


def test_job_lock_prevents_concurrent_runs(instance: Instance, engine, network: FakeApi) -> None:  # type: ignore[no-untyped-def]
    with job_lock(instance.home.root), pytest.raises(JobLockedError):
        ingest(instance.config, instance.home, engine, "ctgov", today=TODAY)


def test_interrupted_runs_are_marked_failed(instance: Instance, engine, network: FakeApi) -> None:  # type: ignore[no-untyped-def]
    with engine.begin() as conn:
        conn.execute(
            insert(ingest_run).values(
                id="run_STALE",
                source="pubmed",
                kind="fetch",
                status="running",
                started_at=datetime(2026, 1, 1, tzinfo=UTC),
                params={},
                counts={},
            )
        )
    ingest(instance.config, instance.home, engine, "ctgov", today=TODAY)
    with engine.connect() as conn:
        row = (
            conn.execute(select(ingest_run).where(ingest_run.c.id == "run_STALE")).mappings().one()
        )
    assert (row["status"], row["error"]) == ("failed", "interrupted")


def test_interruption_keeps_what_was_fetched(
    instance: Instance, engine, network: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("tessera.connectors.pubmed.PubMedConnector.batch_size", 2)
    calls = {"n": 0}

    def efetch(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return httpx.Response(200, content=EFETCH_XML)

    network.route("efetch.fcgi", efetch)
    with pytest.raises(KeyboardInterrupt):
        ingest(
            instance.config,
            instance.home,
            engine,
            "pubmed",
            IngestOptions(ids=["31000001", "31000002", "31000003"]),
            today=TODAY,
        )
    [run] = runs.recent_runs(engine, "pubmed")
    assert run["status"] == "failed" and run["error"].startswith("KeyboardInterrupt")
    with Lake(instance.home.lake) as lake:
        assert lake.scalar("SELECT count(*) FROM pubmed_article") == 4


def test_periodic_publishing(instance: Instance, engine, network: FakeApi, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("tessera.connectors.reporter.PAGE_SIZE", 1)
    ingest(instance.config, instance.home, engine, "reporter", today=TODAY, publish_every=1)
    manifest = read_manifest(instance.home.lake, "reporter_project", 1)
    assert manifest.generation == 3 and len(manifest.files) == 3


def test_linked_pmids_and_icite_auto(instance: Instance, engine, network: FakeApi) -> None:  # type: ignore[no-untyped-def]
    ingest(instance.config, instance.home, engine, "reporter", today=TODAY)
    ingest(instance.config, instance.home, engine, "ctgov", today=TODAY)
    assert linked_pmids_missing(instance.home) == ["31000001", "31000002", "31000003", "31000004"]
    result = ingest(instance.config, instance.home, engine, "pubmed:linked", today=TODAY)
    assert result.plan.mode == "linked" and result.status == "succeeded"
    assert linked_pmids_missing(instance.home) == []
    metrics = ingest(instance.config, instance.home, engine, "icite", today=TODAY)
    assert metrics.report.total_found == 4
    again = ingest(instance.config, instance.home, engine, "icite", today=TODAY)
    assert again.report.total_found == 1  # only the paper iCite doesn't know yet


def test_disabled_source(config_file: Path, network: FakeApi) -> None:
    config_file.write_text(
        config_file.read_text().replace(
            "[sources.ctgov]\nenabled = true", "[sources.ctgov]\nenabled = false"
        )
    )
    config = load_config(config_file, env={})
    home = DataHome(config.data.home)
    home.create()
    engine = make_engine(config.ledger_url)
    migrate.upgrade(engine)
    with pytest.raises(TesseraError, match="disabled"):
        ingest(config, home, engine, "ctgov", today=TODAY)
    results = ingest_many(config, home, engine, sources=("ctgov", "reporter"), today=TODAY)
    assert [r.source for r in results] == ["reporter"]
    engine.dispose()


def test_unknown_source(instance: Instance, engine) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(TesseraError, match="Unknown source"):
        ingest(instance.config, instance.home, engine, "scopes")


# --- CLI -------------------------------------------------------------------------------------


def test_cli_ingest_runs_and_replay(
    instance: Instance, network: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    from tessera.cli import EXIT_OK, EXIT_PARTIAL, main

    assert main(["--config", str(instance.path), "ingest", "reporter"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "reporter (org): succeeded, 3 found" in out
    run_id = out.split("[")[-1].split("]")[0]

    assert (
        main(["--config", str(instance.path), "ingest", "ctgov", "--max-results", "1"])
        == EXIT_PARTIAL
    )
    assert (
        main(["--config", str(instance.path), "ingest", "pubmed", "--ids", "31000001"]) == EXIT_OK
    )
    assert main(["--config", str(instance.path), "replay", "reporter", run_id]) == EXIT_OK
    assert "0 rows written" in capsys.readouterr().out
    assert main(["--config", str(instance.path), "runs", "--limit", "10"]) == EXIT_OK
    listing = capsys.readouterr().out
    assert "replay" in listing and "partial" in listing

    assert main(["--config", str(instance.path), "ingest", "pubmed", "--ids", "abc"]) == 1
    assert main(["--config", str(instance.path), "ingest", "all", "--ids", "1"]) == 1


def test_orcid_reads_directory_members_only(instance: Instance, engine, network: FakeApi) -> None:  # type: ignore[no-untyped-def]
    from tessera.evidence import store
    from tessera.evidence.names import make_name

    member, outsider = "0000-0003-9000-0014", "0000-0003-9000-0022"
    record = orcid_record(member, works=({"pmid": "31000001"}, {"doi": "10.1000/x"}))
    network.routes.update(orcid_api({member: record}).routes)
    with engine.begin() as conn:
        person = store.create_person(
            conn, make_name("Example", "Avery", None), status="active", source="test"
        )
        store.add_identifier(conn, person, "orcid", member, source="roster")
        candidate = store.create_person(
            conn, make_name("Sample", "Blake", None), status="candidate", source="test"
        )
        store.add_identifier(conn, candidate, "orcid", outsider, source="pubmed")
    result = ingest(instance.config, instance.home, engine, "orcid", today=TODAY)
    assert result.status == "succeeded"
    assert result.plan.params["orcids"] == [member]  # discovered people are not read
    with Lake(instance.home.lake) as lake:
        assert lake.scalar("SELECT count(*) FROM orcid_work") == 2
        assert lake.scalar("SELECT status FROM orcid_record") == "public"
    again = ingest(instance.config, instance.home, engine, "orcid", today=TODAY)
    assert again.plan.empty  # read recently; refreshed after the window


def test_papers_listed_on_orcid_records_are_fetched(
    instance: Instance, engine, network: FakeApi
) -> None:  # type: ignore[no-untyped-def]
    from tessera.evidence import store
    from tessera.evidence.names import make_name
    from tessera.jobs.ingest import linked_pmids_missing

    member = "0000-0003-9000-0014"
    record = orcid_record(member, works=({"pmid": "31000005"}, {"doi": "10.1000/no-pmid"}))
    network.routes.update(orcid_api({member: record}).routes)
    with engine.begin() as conn:
        person = store.create_person(
            conn, make_name("Example", "Avery", None), status="active", source="test"
        )
        store.add_identifier(conn, person, "orcid", member, source="roster")
    ingest(instance.config, instance.home, engine, "orcid", today=TODAY)
    assert linked_pmids_missing(instance.home, engine) == ["31000005"]
    assert linked_pmids_missing(instance.home) == []  # without the directory, none
