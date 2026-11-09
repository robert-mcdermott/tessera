"""Run the whole test suite with every ledger on PostgreSQL (PLT-09).

With ``TESSERA_TEST_LEDGER=postgres`` and ``TESSERA_TEST_POSTGRES_URL`` set, every data home a
test creates gets its own empty PostgreSQL database in place of ``ledger.db`` (migrated, or not,
just as the test does to a SQLite file), so the tests that do not name a database run on
PostgreSQL unchanged. A data home created again where one was (a demo rebuilt with ``--force``)
starts from a fresh database; one whose ledger was made before its folder (a restore) keeps it.
Every database made is dropped at the end of the run.

    scripts/test-both.sh        the suite on SQLite, then on a disposable PostgreSQL
"""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

import tessera.config as config_module
import tessera.home as home_module
import tessera.ledger.db as db_module
from tessera.home import DataHome


class Ledgers:
    def __init__(self, base_url: str) -> None:
        self.base = make_url(base_url)
        self.admin = create_engine(base_url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
        self.run = secrets.token_hex(3)
        self.made: dict[str, bool] = {}  # database: whether its data home existed when made
        self._saved: dict[str, Any] = {}

    def _url(self, name: str) -> str:
        return self.base.set(database=name).render_as_string(hide_password=False)

    def _drop(self, name: str) -> None:
        with self.admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))

    def name(self, home: Path) -> str:
        digest = hashlib.sha256(str(Path(home).resolve()).encode()).hexdigest()[:16]
        return f"t_{self.run}_{digest}"

    def url(self, home: Path) -> str:
        name = self.name(home)
        if name not in self.made:
            with self.admin.connect() as conn:
                conn.execute(text(f'CREATE DATABASE "{name}"'))
            self.made[name] = DataHome(Path(home)).marker.exists()
        return self._url(name)

    def fresh(self, home: Path) -> None:
        """A data home is being created where none is: forget the database of one that was here
        before, but keep one made for this new home ahead of its folder."""
        name = self.name(home)
        if self.made.get(name):
            self._drop(name)
            del self.made[name]

    def install(self) -> None:
        ledgers = self
        original_url = config_module.Config.ledger_url
        original_create = home_module.DataHome.create
        original_engine = db_module.create_engine

        def ledger_url(config: Any) -> str:
            return config.ledger.url or ledgers.url(config.data.home)

        def create(home: Any) -> None:
            if not home.marker.exists():
                ledgers.fresh(home.root)
            original_create(home)

        def engine_for(url: Any, **kwargs: Any) -> Any:
            # Tests make many installations and do not always dispose of their engines, so hold
            # no idle connections that would add up across the run.
            if make_url(url).get_backend_name() == "postgresql":
                kwargs["poolclass"] = NullPool
            return original_engine(url, **kwargs)

        self._saved = {"url": original_url, "create": original_create, "engine": original_engine}
        config_module.Config.ledger_url = property(ledger_url)  # type: ignore[method-assign,assignment]
        home_module.DataHome.create = create  # type: ignore[method-assign]
        db_module.create_engine = engine_for  # type: ignore[assignment]

    def uninstall(self) -> None:
        if self._saved:
            config_module.Config.ledger_url = self._saved["url"]  # type: ignore[method-assign]
            home_module.DataHome.create = self._saved["create"]  # type: ignore[method-assign]
            db_module.create_engine = self._saved["engine"]
        for name in self.made:
            self._drop(name)
        self.admin.dispose()
