"""``tessera doctor``: check that an installation is ready to run."""

from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb
import httpx

from tessera import __version__
from tessera.api.static import WEB_ROOT
from tessera.config import Config, config_warnings, load_config
from tessera.core.errors import TesseraError
from tessera.home import DataHome
from tessera.ledger import migrate
from tessera.ledger.db import make_engine

from .output import FAIL, OK, WARN, mark

# Lightweight endpoints used only to test reachability (doctor --online).
SOURCE_PROBES = {
    "pubmed": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi?retmode=json",
    "icite": "https://icite.od.nih.gov/api/pubs?pmids=1&format=json",
    "reporter": "https://api.reporter.nih.gov/",
    "ctgov": "https://clinicaltrials.gov/api/v2/version",
}


@dataclass
class Check:
    name: str
    state: str
    detail: str


def run_checks(config_path: str | None, *, online: bool = False) -> list[Check]:
    checks: list[Check] = []
    version_ok = sys.version_info >= (3, 12)
    checks.append(
        Check("Python", OK if version_ok else FAIL, f"{sys.version.split()[0]} (needs 3.12+)")
    )

    try:
        config = load_config(config_path)
    except TesseraError as exc:
        checks.append(Check("Configuration", FAIL, str(exc)))
        return checks
    checks.append(Check("Configuration", OK, str(config.path)))
    checks += [Check("Configuration", WARN, w) for w in config_warnings(config)]

    checks.append(_check_home(config))
    checks.append(_check_ledger(config))
    checks.append(_check_access(config))
    checks.append(_check_fts5())
    checks.append(_check_duckdb())
    index = WEB_ROOT / "index.html"
    checks.append(
        Check(
            "Web app",
            OK if index.is_file() else FAIL,
            str(WEB_ROOT) if index.is_file() else f"missing {index}",
        )
    )
    if online:
        checks += _check_sources(config)
    return checks


def _check_home(config: Config) -> Check:
    home = DataHome(config.data.home)
    if not home.is_initialized():
        return Check("Data home", FAIL, f"{home.root} is not initialized; run `tessera init`")
    if not os.access(home.root, os.W_OK):
        return Check("Data home", FAIL, f"{home.root} is not writable")
    return Check("Data home", OK, str(home.root))


def _check_ledger(config: Config) -> Check:
    try:
        engine = make_engine(config.ledger_url)
    except TesseraError as exc:
        return Check("Ledger", FAIL, str(exc))
    try:
        current = migrate.current_revision(engine)
    except Exception as exc:
        return Check("Ledger", FAIL, f"cannot connect: {exc.__class__.__name__}: {exc}")
    finally:
        engine.dispose()
    head = migrate.head_revision()
    where = engine.url.render_as_string(hide_password=True)
    if current is None:
        return Check("Ledger", FAIL, f"{where} has no schema; run `tessera db upgrade`")
    if current != head:
        return Check(
            "Ledger", FAIL, f"at revision {current}, expected {head}; run `tessera db upgrade`"
        )
    return Check("Ledger", OK, f"{where} (revision {current})")


def _check_access(config: Config) -> Check:
    if not config.server.accounts:
        return Check("Access", OK, "single-operator mode: no sign-in, this machine only")
    try:
        from tessera.auth.accounts import active_admins

        engine = make_engine(config.ledger_url)
        try:
            admins = active_admins(engine)
        finally:
            engine.dispose()
    except Exception as exc:  # the ledger check reports the cause
        return Check(
            "Access", WARN, f"accounts on; could not count admins ({exc.__class__.__name__})"
        )
    if not admins:
        return Check(
            "Access",
            FAIL,
            "accounts on but no active admin; run `tessera users add NAME --role admin`",
        )
    return Check("Access", OK, f"accounts on, {admins} active admin{'s' if admins != 1 else ''}")


def _check_fts5() -> Check:
    try:
        con = sqlite3.connect(":memory:")
        con.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='porter unicode61')")
        con.close()
    except sqlite3.Error as exc:
        return Check("SQLite FTS5", FAIL, f"unavailable: {exc}")
    return Check("SQLite FTS5", OK, f"SQLite {sqlite3.sqlite_version}")


def _check_duckdb() -> Check:
    con = duckdb.connect(":memory:")
    try:
        row = con.execute(
            "SELECT loaded FROM duckdb_extensions() WHERE extension_name = 'parquet'"
        ).fetchone()
    finally:
        con.close()
    if not row or not row[0]:
        return Check("DuckDB Parquet", FAIL, "Parquet support is not built in to this DuckDB")
    return Check("DuckDB Parquet", OK, f"DuckDB {duckdb.__version__}, built in")


def _check_sources(config: Config) -> list[Check]:
    contact = config.sources.contact_email or "unconfigured"
    headers = {"User-Agent": f"Tessera/{__version__} (+mailto:{contact})"}
    results = []
    with httpx.Client(timeout=10.0, headers=headers, follow_redirects=True) as client:
        for name, url in SOURCE_PROBES.items():
            if not getattr(config.sources, name).enabled:
                results.append(Check(f"Source: {name}", OK, "disabled in configuration"))
                continue
            try:
                response = client.get(url)
            except httpx.HTTPError as exc:
                results.append(Check(f"Source: {name}", FAIL, f"unreachable: {exc}"))
                continue
            state = OK if response.status_code < 500 else WARN
            results.append(Check(f"Source: {name}", state, f"HTTP {response.status_code}"))
    return results


def print_checks(checks: list[Check]) -> None:
    width = max(len(c.name) for c in checks)
    for check in checks:
        print(f"{mark(check.state)} {check.name.ljust(width)}  {check.detail}")


def exit_code(checks: list[Check]) -> int:
    return 1 if any(c.state == FAIL for c in checks) else 0


__all__ = ["Check", "Path", "exit_code", "print_checks", "run_checks"]
