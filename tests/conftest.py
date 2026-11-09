from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tessera.api.app import create_app
from tessera.config import Config, example_config_text, load_config
from tessera.home import DataHome
from tessera.ledger import migrate
from tessera.ledger.db import make_engine


@dataclass
class Instance:
    path: Path
    config: Config
    home: DataHome


_LEDGERS = None


def pytest_configure(config: pytest.Config) -> None:
    """With TESSERA_TEST_LEDGER=postgres, every test's ledger is a PostgreSQL database."""
    import os

    global _LEDGERS
    if os.environ.get("TESSERA_TEST_LEDGER") == "postgres":
        url = os.environ.get("TESSERA_TEST_POSTGRES_URL")
        if not url:
            raise pytest.UsageError("TESSERA_TEST_LEDGER=postgres needs TESSERA_TEST_POSTGRES_URL")
        from tests.pg_ledgers import Ledgers

        _LEDGERS = Ledgers(url)
        _LEDGERS.install()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if _LEDGERS is None:
        return
    skip = pytest.mark.skip(reason="about the default SQLite ledger; this run is on PostgreSQL")
    for item in items:
        if "sqlite_ledger" in item.keywords:
            item.add_marker(skip)


def pytest_unconfigure(config: pytest.Config) -> None:
    if _LEDGERS is not None:
        _LEDGERS.uninstall()


def pytest_report_header(config: pytest.Config) -> str:
    return f"ledger: {'PostgreSQL' if _LEDGERS is not None else 'SQLite'}"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's TESSERA_* variables from leaking into tests."""
    import os

    for name in list(os.environ):
        if name.startswith("TESSERA_") and name != "TESSERA_TEST_POSTGRES_URL":
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "tessera.toml"
    path.write_text(example_config_text(), encoding="utf-8")
    return path


@pytest.fixture
def instance(config_file: Path) -> Instance:
    """A configured installation with an initialized data home and migrated ledger."""
    config = load_config(config_file, env={})
    home = DataHome(config.data.home)
    home.create()
    engine = make_engine(config.ledger_url)
    migrate.upgrade(engine)
    engine.dispose()
    return Instance(config_file, config, home)


@pytest.fixture
def client(instance: Instance) -> Iterator[TestClient]:
    with TestClient(create_app(instance.config), base_url="http://localhost") as test_client:
        yield test_client
