"""Engine creation for the ledger."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url

from tessera.core.errors import LedgerError


def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")  # readers never block the writer
        cursor.execute("PRAGMA synchronous=NORMAL")  # safe with WAL, much faster than FULL
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def make_engine(url: str, *, echo: bool = False) -> Engine:
    """Create an engine for a ``sqlite:///`` or ``postgresql+psycopg://`` URL."""
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        database = parsed.database or ""
        if database and database != ":memory:":
            parent = Path(database).parent
            if not parent.is_dir():
                raise LedgerError(f"Ledger directory {parent} does not exist. Run `tessera init`.")
        engine = create_engine(url, echo=echo)
        event.listen(engine, "connect", _sqlite_pragmas)
        return engine
    if parsed.get_backend_name() == "postgresql":
        try:
            return create_engine(url, echo=echo, pool_pre_ping=True)
        except ModuleNotFoundError as exc:
            raise LedgerError(
                "PostgreSQL support needs the optional driver: "
                "install Tessera with the `postgres` extra."
            ) from exc
    raise LedgerError(f"Unsupported ledger URL {parsed.render_as_string(hide_password=True)}")
