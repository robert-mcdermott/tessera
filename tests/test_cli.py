from __future__ import annotations

from pathlib import Path

import pytest

from tessera import __version__
from tessera.cli import EXIT_ERROR, EXIT_OK, main
from tessera.lake import LakeWriter
from tessera.lake.schemas import ICITE

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
    assert (home / "ledger.db").is_file()
    assert "Ledger ready (revision 0001)" in out

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
    assert "revision 0001" in out
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
