from __future__ import annotations

from pathlib import Path

import pytest

from tessera import __version__
from tessera.cli import EXIT_ERROR, EXIT_OK, main
from tessera.config import load_config
from tessera.lake import LakeWriter
from tessera.lake.schemas import ICITE
from tessera.ledger import migrate

from .conftest import Instance


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys)
    assert code == EXIT_OK
    assert "init" in out and "serve" in out


def test_init_creates_everything(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "site" / "tessera.toml"
    code, out = run(capsys, "--config", str(config), "init")
    assert code == EXIT_OK
    assert config.is_file()
    home = tmp_path / "site" / "var"
    for sub in ("raw", "lake", "derived", "logs"):
        assert (home / sub).is_dir()
    if load_config(config, env={}).ledger_url.startswith("sqlite"):
        assert (home / "ledger.db").is_file()
    assert f"Ledger ready (revision {migrate.head_revision()})" in out

    config.write_text(config.read_text().replace('name = "Example University"', 'name = "Kept"'))
    code, out = run(capsys, "--config", str(config), "init")
    assert code == EXIT_OK
    assert "Using existing configuration" in out
    assert 'name = "Kept"' in config.read_text()


def test_doctor_healthy(instance: Instance, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "--config", str(instance.path), "doctor")
    assert code == EXIT_OK, out
    for name in ("Python", "Configuration", "Data home", "Ledger", "SQLite FTS5", "DuckDB Parquet"):
        assert name in out


def test_doctor_checks_what_a_production_server_needs(
    instance: Instance, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NCBI_API_KEY", raising=False)
    code, out = run(capsys, "--config", str(instance.path), "doctor")
    assert "Production" not in out  # only when asked
    code, out = run(capsys, "--config", str(instance.path), "doctor", "--production")
    assert code == EXIT_ERROR, out  # no accounts: a server others use needs them
    lines = {
        line.split("  ")[0][2:].strip(): line for line in out.splitlines() if "Production" in line
    }
    assert lines["Production: sign-in"].startswith("✗")
    assert lines["Production: NCBI key"].startswith("!")
    assert "Production: disk" in lines and "Production: sync" in lines
    monkeypatch.setenv("NCBI_API_KEY", "a-test-key")
    config = instance.path.read_text().replace("accounts = false", "accounts = true")
    instance.path.write_text(config)
    from tessera.auth.accounts import Grant, create_account
    from tessera.config import load_config
    from tessera.ledger.db import make_engine

    engine = make_engine(load_config(instance.path).ledger_url)
    try:
        create_account(engine, "admin", "Admin", "a-long-test-passphrase-9",
                       [Grant("admin", None, None, False)], actor="test")  # fmt: skip
    finally:
        engine.dispose()
    code, out = run(capsys, "--config", str(instance.path), "doctor", "--production")
    assert "✓ Production: sign-in" in out and "✓ Production: NCBI key" in out


def test_serve_says_when_the_port_is_taken(
    instance: Instance, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rather than saying it is running and then failing to listen."""
    import socket

    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        code, out = run(capsys, "--config", str(instance.path), "serve", "--port", str(port))
    assert code == EXIT_ERROR
    assert f"already listening on 127.0.0.1:{port}" in out and "--port" in out
    assert "running at" not in out


def test_doctor_reports_missing_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "--config", str(tmp_path / "absent.toml"), "doctor")
    assert code == EXIT_ERROR
    assert "tessera init" in out


def test_doctor_reports_unmigrated_ledger(
    config_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from tessera.config import load_config
    from tessera.home import DataHome

    DataHome(load_config(config_file, env={}).data.home).create()
    code, out = run(capsys, "--config", str(config_file), "doctor")
    assert code == EXIT_ERROR
    assert "tessera db upgrade" in out


def test_db_commands(config_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tessera.config import load_config
    from tessera.home import DataHome

    DataHome(load_config(config_file, env={}).data.home).create()
    code, out = run(capsys, "--config", str(config_file), "db", "current")
    assert code == EXIT_ERROR
    assert "none" in out
    code, out = run(capsys, "--config", str(config_file), "db", "upgrade")
    assert code == EXIT_OK
    assert f"revision {migrate.head_revision()}" in out
    code, out = run(capsys, "--config", str(config_file), "db", "current")
    assert code == EXIT_OK
    assert "up to date" in out


def test_lake_commands(instance: Instance, capsys: pytest.CaptureFixture[str]) -> None:
    for run_id, count in (("run_1", 1), ("run_2", 2)):
        with LakeWriter(instance.home.lake, ICITE, run_id) as writer:
            writer.add([{"pmid": "1", "citation_count": count}])
    code, out = run(capsys, "--config", str(instance.path), "lake", "status")
    assert code == EXIT_OK
    assert "icite_metrics" in out
    code, out = run(capsys, "--config", str(instance.path), "lake", "compact", "icite")
    assert code == EXIT_OK
    assert "icite_metrics: 2 files / 2 rows -> 1 files / 1 rows" in out
    code, out = run(capsys, "--config", str(instance.path), "lake", "compact", "bogus")
    assert code == EXIT_ERROR
    assert "Unknown dataset" in out
    code, out = run(capsys, "--config", str(instance.path), "lake", "gc", "--retention-days", "0")
    assert code == EXIT_OK
    assert "Removed 2 superseded files" in out


def test_commands_need_initialized_home(
    config_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = run(capsys, "--config", str(config_file), "lake", "status")
    assert code == EXIT_ERROR
    assert "tessera init" in out


def test_damaged_data_files_say_what_to_do(
    instance: Instance, capsys: pytest.CaptureFixture[str]
) -> None:
    """Wave 8's failure drills: a damaged search index or derived file is named by doctor with
    the command that rebuilds it; the app answers 503 saying the same, not an unexplained 500;
    and the command line says to run doctor rather than showing a traceback."""
    from fastapi.testclient import TestClient

    from tessera.api.app import create_app
    from tessera.search.index import index_path

    index = index_path(instance.home)
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_bytes(b"\x00not a database\x00" * 200)
    derived = instance.home.derived / "concept_profile.parquet"
    derived.write_bytes(b"PAR1" + b"\x00damaged\x00" * 100 + b"PAR1")
    code, out = run(capsys, "--config", str(instance.path), "doctor")
    assert code == EXIT_ERROR
    assert "Search index" in out and "tessera build search" in out
    assert "concept_profile.parquet" in out and "tessera build discovery" in out
    with TestClient(create_app(instance.config), base_url="http://127.0.0.1") as client:
        answer = client.get("/api/v1/people")
        assert answer.status_code == 503 and "tessera build search" in answer.json()["detail"]
    code, out = run(capsys, "--config", str(instance.path), "people", "list")
    assert "Traceback" not in out
    code, out = run(capsys, "--config", str(instance.path), "build", "search")
    assert code == EXIT_OK, out
    code, out = run(capsys, "--config", str(instance.path), "doctor")
    assert "✓ Search index" in out
