"""Run ledger migrations programmatically."""

from __future__ import annotations

from collections.abc import Callable

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Engine

from tessera.core.errors import LedgerError
from tessera.ledger.db import set_sqlite_pragma

SCRIPT_LOCATION = "tessera.ledger:migrations"


def alembic_config(url: str) -> AlembicConfig:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", SCRIPT_LOCATION)
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def head_revision(url: str = "sqlite://") -> str | None:
    """The newest migration shipped with this version of Tessera."""
    return ScriptDirectory.from_config(alembic_config(url)).get_current_head()


def current_revision(engine: Engine) -> str | None:
    """The migration the database is at, or None for an empty database."""
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def _run(engine: Engine, step: Callable[[AlembicConfig, str], None], revision: str) -> None:
    """Run a migration step in one transaction.

    SQLite cannot alter a constraint in place, so a migration rebuilds the table (create,
    copy, drop, rename). With foreign keys on, dropping the old table would cascade-delete
    every row that references it (names, identifiers). Foreign keys are therefore switched
    off for the migration, as SQLite's documentation prescribes for table rebuilds, and the
    result is checked before it is committed.
    """
    cfg = alembic_config(engine.url.render_as_string(hide_password=False))
    with engine.connect() as conn:
        sqlite = conn.dialect.name == "sqlite"
        if sqlite:  # a no-op inside a transaction, so it must come first
            set_sqlite_pragma(conn, "foreign_keys=OFF")
        try:
            with conn.begin():
                cfg.attributes["connection"] = conn
                step(cfg, revision)
                if sqlite:
                    broken = conn.exec_driver_sql("PRAGMA foreign_key_check").all()
                    if broken:
                        raise LedgerError(
                            f"Migration to {revision} left {len(broken)} rows with broken "
                            f"references (first: {tuple(broken[0])}); nothing was changed."
                        )
        finally:
            if sqlite:
                set_sqlite_pragma(conn, "foreign_keys=ON")


def upgrade(engine: Engine, revision: str = "head") -> None:
    _run(engine, command.upgrade, revision)


def downgrade(engine: Engine, revision: str) -> None:
    """Step the schema back (used by tests and by operators rolling back an upgrade)."""
    _run(engine, command.downgrade, revision)


def is_current(engine: Engine) -> bool:
    return current_revision(engine) == head_revision()
