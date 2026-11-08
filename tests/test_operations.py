"""Backup, restore, and the full exit export (PLT-08); operational status and alerts (PLT-12)."""

from __future__ import annotations

import dataclasses
import io
import json
import os
import tarfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select, text

from tessera.api.app import create_app
from tessera.config import load_config
from tessera.core.clock import utc_now
from tessera.demo import build_demo
from tessera.home import DataHome
from tessera.jobs.build import build_search
from tessera.ledger import migrate
from tessera.ledger.db import make_engine
from tessera.ledger.tables import ingest_run, metadata
from tessera.ops import backup, exit, status

TODAY = date(2026, 11, 7)
SKIP = {"ingest_run", "setting"}  # runs and the last backup are written after the archive


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[object, Path]]:
    path = tmp_path_factory.mktemp("ops-demo")
    result = build_demo(path, today=TODAY)
    yield load_config(Path(result["config"]), env={}), path


def elsewhere(config, root: Path):  # type: ignore[no-untyped-def]
    """The same configuration with a new, empty data home."""
    return dataclasses.replace(config, data=dataclasses.replace(config.data, home=root / "var"))


def rows(config) -> dict[str, list[dict]]:  # type: ignore[no-untyped-def]
    engine = make_engine(config.ledger_url)
    try:
        with engine.connect() as conn:
            return {
                t.name: [dict(r) for r in conn.execute(
                    select(t).order_by(*t.primary_key.columns)).mappings()]
                for t in metadata.sorted_tables if t.name not in SKIP
            }  # fmt: skip
    finally:
        engine.dispose()


def make_backup(config, tmp_path: Path) -> backup.BackupResult:  # type: ignore[no-untyped-def]
    engine = make_engine(config.ledger_url)
    try:
        return backup.create_backup(config, DataHome(config.data.home), engine,
                                    out=tmp_path / "backup.tar.gz")  # fmt: skip
    finally:
        engine.dispose()


def test_a_backup_restores_into_a_new_data_home_exactly(demo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    config, _ = demo
    made = make_backup(config, tmp_path)
    assert made.path.is_file() and made.tables["assertion"] > 500
    checked = backup.verify_backup(made.path)
    assert checked.ok, checked.problems
    manifest = checked.manifest
    assert manifest["ledger"]["revision"] == migrate.head_revision()
    assert {"ledger", "lake", "config"} <= set(manifest["includes"])
    assert not any(name.startswith("raw/") for name in manifest["files"])

    target = elsewhere(config, tmp_path / "restored")
    home = DataHome(target.data.home)
    home.root.mkdir(parents=True)
    engine = make_engine(target.ledger_url)
    try:
        result = backup.restore_backup(target, home, engine, made.path)
        assert result.tables == made.tables
        build_search(target, home, engine)
    finally:
        engine.dispose()
    assert rows(target) == rows(config)  # every decision, name, link, page, and report
    original = sorted(p.relative_to(config.data.home / "lake").as_posix()
                      for p in (config.data.home / "lake").rglob("*.parquet"))  # fmt: skip
    restored = sorted(p.relative_to(home.lake).as_posix() for p in home.lake.rglob("*.parquet"))
    assert restored == original

    def answers(cfg) -> dict:  # type: ignore[no-untyped-def]
        with TestClient(create_app(cfg), base_url="http://localhost") as client:
            return {path: client.get(path).json()["data"] for path in (
                "/api/v1/groups?limit=200", "/api/v1/review?limit=50",
                "/api/v1/insight/figures/funding.awarded?start=2022&end=2026",
            )}  # fmt: skip

    def plain(value):  # type: ignore[no-untyped-def]
        if isinstance(value, dict):
            return {k: plain(v) for k, v in value.items()
                    if k not in ("generated_at", "last_success_at", "complete_from")}  # fmt: skip
        return [plain(v) for v in value] if isinstance(value, list) else value

    assert plain(answers(target)) == plain(answers(config))


def test_a_damaged_or_foreign_backup_is_refused(demo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    config, _ = demo
    made = make_backup(config, tmp_path)
    damaged = tmp_path / "damaged.tar.gz"
    with tarfile.open(made.path, "r:gz") as source, tarfile.open(damaged, "w:gz") as out:
        for member in source.getmembers():
            data = source.extractfile(member).read()  # type: ignore[union-attr]
            if member.name == "ledger/person.jsonl":
                data = data.replace(b'"active"', b'"former"', 1)
            member.size = len(data)
            out.addfile(member, io.BytesIO(data))
    result = backup.verify_backup(damaged)
    assert not result.ok and "ledger/person.jsonl does not match its digest." in result.problems

    target = elsewhere(config, tmp_path / "again")
    home = DataHome(target.data.home)
    home.create()
    engine = make_engine(target.ledger_url)
    try:
        with pytest.raises(backup.BackupError, match="does not match its manifest"):
            backup.restore_backup(target, home, engine, damaged)
        backup.restore_backup(target, home, engine, made.path)
        with pytest.raises(backup.BackupError, match="empty data home and ledger"):
            backup.restore_backup(target, home, engine, made.path)  # not into a used one
    finally:
        engine.dispose()

    newer = tmp_path / "newer.tar.gz"
    with tarfile.open(made.path, "r:gz") as source, tarfile.open(newer, "w:gz") as out:
        for member in source.getmembers():
            data = source.extractfile(member).read()  # type: ignore[union-attr]
            if member.name == "manifest.json":
                manifest = json.loads(data)
                manifest["ledger"]["revision"] = "9999"
                data = json.dumps(manifest).encode()
            member.size = len(data)
            out.addfile(member, io.BytesIO(data))
    fresh = elsewhere(config, tmp_path / "fresh")
    DataHome(fresh.data.home).create()
    engine = make_engine(fresh.ledger_url)
    try:
        with pytest.raises(backup.BackupError, match="does not know"):
            backup.restore_backup(fresh, DataHome(fresh.data.home), engine, newer)
    finally:
        engine.dispose()


@pytest.mark.skipif(not os.environ.get("TESSERA_TEST_POSTGRES_URL"),
                    reason="set TESSERA_TEST_POSTGRES_URL to run PostgreSQL tests")  # fmt: skip
def test_a_sqlite_backup_restores_into_postgresql(demo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    config, _ = demo
    made = make_backup(config, tmp_path)
    url = os.environ["TESSERA_TEST_POSTGRES_URL"]
    target = dataclasses.replace(
        elsewhere(config, tmp_path / "pg"), ledger=dataclasses.replace(config.ledger, url=url)
    )
    home = DataHome(target.data.home)
    home.create()
    engine = make_engine(url)

    def reset() -> None:  # a disposable test database: start from nothing
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))

    try:
        reset()
        backup.restore_backup(target, home, engine, made.path)
        assert rows(target) == rows(config)
        events = metadata.tables["person_event"]
        with engine.begin() as conn:  # integer keys keep counting after the restored rows
            someone = conn.execute(select(metadata.tables["person"].c.id).limit(1)).scalar()
            before = conn.execute(select(func.max(events.c.id))).scalar()
            made_id = conn.execute(insert(events).values(
                person_id=someone, actor="test", action="undo", at=utc_now())
                .returning(events.c.id)).scalar()  # fmt: skip
        assert made_id == before + 1
    finally:
        reset()
        engine.dispose()


def test_the_exit_export_needs_nothing_but_open_formats(demo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    config, _ = demo
    engine = make_engine(config.ledger_url)
    try:
        result = exit.write_exit(config, DataHome(config.data.home), engine, tmp_path / "exit")
    finally:
        engine.dispose()
    out = result.out
    assert (out / "README.md").is_file() and (out / "DATA-DICTIONARY.md").is_file()
    assert result.ledger["assertion"] > 500 and result.lake["pubmed_article"] > 500
    con = duckdb.connect()
    papers = con.sql(f"SELECT count(*) FROM '{out / 'lake' / 'pubmed_article.parquet'}'").fetchall()
    assert papers[0][0] == result.lake["pubmed_article"]
    links = con.sql(
        f"SELECT predicate, count(*) FROM read_csv_auto('{out / 'ledger' / 'assertion.csv'}') "
        "GROUP BY predicate"
    ).fetchall()
    assert {"authored", "investigator_on", "member_of"} <= {p for p, _ in links}
    header = (out / "ledger" / "account.csv").read_text().splitlines()[0]
    assert "password_hash" not in header and "username" in header
    assert not (out / "ledger" / "auth_session.csv").exists()
    dictionary = (out / "DATA-DICTIONARY.md").read_text()
    for table in metadata.sorted_tables:
        assert exit.LEDGER_NOTES.get(table.name) or table.name in exit.SKIPPED_TABLES, table.name
        if table.name in exit.OPTIONAL_TABLES:  # the assistant is off: no trace of its log
            assert f"### ledger/{table.name}" not in dictionary
        elif table.name not in exit.SKIPPED_TABLES:
            assert f"### ledger/{table.name}" in dictionary
    for name in result.lake:
        assert exit.LAKE_NOTES.get(name), f"no note for {name}"
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["files"]["ledger/assertion.parquet"]["sha256"]
    with pytest.raises(exit.ExitError, match="not empty"):
        engine = make_engine(config.ledger_url)
        try:
            exit.write_exit(config, DataHome(config.data.home), engine, out)
        finally:
            engine.dispose()


def test_operations_show_runs_sizes_and_issues_and_alert_once(demo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    config, _ = demo
    home = DataHome(config.data.home)
    engine = make_engine(config.ledger_url)
    sent: list[list] = []
    monkeypatch.setattr(
        status, "send", lambda mail, messages: sent.append(messages) or len(messages)
    )
    alerting = dataclasses.replace(
        config,
        alerts=dataclasses.replace(config.alerts, email=("ops@corvane.example",), backup_days=0),
        mail=dataclasses.replace(config.mail, host="smtp.corvane.example",
                                 sender="Tessera <tessera@corvane.example>"),
    )  # fmt: skip
    try:
        now = status.status(alerting, home, engine)
        pubmed = next(s for s in now["sources"] if s["source"] == "pubmed")
        assert pubmed["runs"] >= 1 and pubmed["last_status"] == "succeeded"
        assert now["sizes"]["lake"] > 0 and now["sizes"]["ledger"] > 0
        assert not now["job_running"] and now["issues"] == []
        assert status.alert(alerting, engine, home) == ([], None, False)

        with engine.begin() as conn:
            conn.execute(insert(ingest_run).values(
                id="run_TESTFAILURE", source="pubmed", kind="fetch", status="failed",
                started_at=utc_now(), finished_at=utc_now(), params={}, counts={},
                error="PubMed answered 503 Service Unavailable"))  # fmt: skip
        found, mail, was_sent = status.alert(alerting, engine, home)
        assert was_sent and len(sent) == 1 and mail["To"] == "ops@corvane.example"
        assert found[0]["kind"] == "failure" and "503" in mail.get_content()
        assert status.alert(alerting, engine, home)[2] is False  # the same issue, not again
        assert status.alert(alerting, engine, home, force=True)[2] is True

        watched = dataclasses.replace(alerting, alerts=dataclasses.replace(
            alerting.alerts, backup_days=7))  # fmt: skip
        with engine.begin() as conn:  # the last backup was a month ago
            backup._remember(conn, {"at": "2026-10-01T00:00:00Z", "path": "old.tar.gz",
                                    "bytes": 1, "revision": "0013"})  # fmt: skip
        assert any(i["kind"] == "backup" for i in status.issues(watched, home, engine))
    finally:
        engine.dispose()
    with TestClient(create_app(config), base_url="http://localhost") as client:
        data = client.get("/api/v1/operations").json()["data"]
    assert {"sources", "recent", "sizes", "backup", "issues"} <= set(data)
    assert data["recent"][0]["status"] in ("succeeded", "failed", "partial")


def test_doctor_warns_until_there_is_a_backup(demo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from tessera.cli.doctor import OK, WARN, _check_backup

    config, _ = demo
    assert _check_backup(config).state in (OK, WARN)
    make_backup(config, tmp_path)
    check = _check_backup(config)
    assert check.state == OK and "backup.tar.gz" in check.detail
