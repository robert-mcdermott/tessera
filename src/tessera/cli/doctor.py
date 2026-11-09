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


def run_checks(
    config_path: str | None, *, online: bool = False, production: bool = False
) -> list[Check]:
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
    checks.append(_check_backup(config))
    checks += _check_brand(config)
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
    if config.ai.enabled:  # nothing is said of the assistant unless it is turned on
        checks.append(_check_ai(config))
    if config.auth.oidc.enabled:
        checks += _check_sso(config, online=online)
    if online:
        checks += _check_sources(config)
    if production:
        checks += _check_production(config)
    return checks


def _check_ai(config: Config) -> Check:
    import os

    ai = config.ai
    where = f"{ai.provider} · {ai.model} at {ai.url}"
    if ai.provider == "bedrock":
        token = os.environ.get(ai.api_key_env or "AWS_BEARER_TOKEN_BEDROCK")
        keys = os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")
        if not (token or keys):
            return Check("Assistant", FAIL, f"{where}: set AWS_BEARER_TOKEN_BEDROCK, or "
                         "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY")  # fmt: skip
        return Check("Assistant", OK, where)
    official = not ai.base_url
    if official and not os.environ.get(ai.key_env):
        return Check("Assistant", FAIL, f"{where}: set {ai.key_env} (the official service "
                     "needs a key)")  # fmt: skip
    keyed = "with a key" if os.environ.get(ai.key_env) else "without a key"
    return Check("Assistant", OK, f"{where} ({keyed}); `tessera ai check` tries it")


def _check_sso(config: Config, *, online: bool) -> list[Check]:
    """Single sign-on: its secret in the environment, and (online) the provider's discovery
    document and signing keys."""
    oidc = config.auth.oidc
    out = []
    if os.environ.get(oidc.client_secret_env):
        detail = f"{oidc.issuer}; the secret is in {oidc.client_secret_env}"
        out.append(Check("Single sign-on", OK, detail))
    else:
        detail = f"{oidc.client_secret_env} is not set: only a public client can sign in"
        out.append(Check("Single sign-on", WARN, detail))
    if not online:
        return out
    from tessera.auth.oidc import OIDCError, Provider

    provider = Provider(oidc.issuer)
    try:
        meta = provider.metadata()
        keys = [k for k in provider.signing_keys() if k.get("kty") == "RSA"]
    except OIDCError as exc:
        return [*out, Check("Single sign-on provider", FAIL, str(exc))]
    algs = meta.get("id_token_signing_alg_values_supported") or ["RS256"]
    if not keys or not any(a in ("RS256", "RS384", "RS512") for a in algs):
        detail = "the provider offers no RSA keys for ID tokens; set it to sign them with RS256"
        return [*out, Check("Single sign-on provider", FAIL, detail)]
    detail = f"discovery and {len(keys)} RSA key(s) read; ID tokens signed with {', '.join(algs)}"
    return [*out, Check("Single sign-on provider", OK, detail)]


def _check_backup(config: Config) -> Check:
    """Warn when there is no recent backup (``[alerts] backup_days``)."""
    from datetime import datetime, timedelta

    from tessera.core.clock import utc_now
    from tessera.ops.backup import last_backup

    if not config.alerts.backup_days:
        return Check("Backups", OK, "not watched ([alerts] backup_days = 0)")
    try:
        engine = make_engine(config.ledger_url)
        try:
            backup = last_backup(engine) if migrate.is_current(engine) else None
        finally:
            engine.dispose()
    except Exception:
        return Check("Backups", WARN, "cannot read the ledger to find the last backup")
    if backup is None:
        return Check("Backups", WARN, "no backup yet; run `tessera backup` (docs/operations.md)")
    age = utc_now() - datetime.fromisoformat(backup["at"])
    detail = f"last {backup['at'][:16].replace('T', ' ')} UTC, {backup['path']}"
    if age > timedelta(days=config.alerts.backup_days):
        return Check("Backups", WARN, f"{detail}: older than {config.alerts.backup_days} days")
    return Check("Backups", OK, detail)


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


def _check_brand(config: Config) -> list[Check]:
    """Every pair of brand colors that carries text meets WCAG AA, by day and by night."""
    from tessera.branding import PAIRS, contrast_problems

    problems = contrast_problems(config)
    if problems:
        return [Check("Brand colors", FAIL, p) for p in problems]
    source = "your palette" if config.branding.palette or config.branding.accent_color else (
        "the default palette")  # fmt: skip
    return [
        Check(
            "Brand colors",
            OK,
            f"{len(PAIRS) * 2} color pairs of {source} meet WCAG AA, by day and by night",
        )
    ]


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


# --- production ----------------------------------------------------------------------------------

SYNC_DAYS = 2  # a nightly sync older than this has stopped running
FREE_GB = 5  # at least this much free on the data home's disk, and twice the ledger and indexes


def _check_production(config: Config) -> list[Check]:
    """What a server other people use needs beyond what every installation does
    (``tessera doctor --production``)."""
    import shutil
    from datetime import UTC, timedelta

    from tessera.core.clock import utc_now
    from tessera.jobs import runs
    from tessera.search.index import index_path

    checks: list[Check] = []

    def add(what: str, state: str, detail: str) -> None:
        checks.append(Check(f"Production: {what}", state, detail))

    if config.server.accounts:
        add("sign-in", OK, "accounts on")
    else:
        add("sign-in", FAIL, "a server others use needs [server] accounts = true")
    if config.sources.contact_email:
        add("contact", OK, config.sources.contact_email)
    else:
        add("contact", WARN, "set [sources] contact_email: NCBI and others ask who is calling")
    key = config.sources.pubmed.api_key_env
    if key and os.environ.get(key):
        add("NCBI key", OK, f"{key} is set")
    else:
        where = key or "[sources.pubmed] api_key_env"
        add(
            "NCBI key",
            WARN,
            f"no NCBI API key in {where}: PubMed allows 3 requests a second, 10 with one",
        )
    for name, url in (("app", config.mail.base_url), ("public site", config.publish.base_url)):
        if url and not url.startswith("https://"):
            add("HTTPS", WARN, f"the {name}'s address {url} is not HTTPS")
    home = DataHome(config.data.home)
    if home.is_initialized():
        if index_path(home).is_file():
            add("search", OK, "the search index is built")
        else:
            add("search", WARN, "no search index: run the first sync or `tessera build search`")
        used = sum(p.stat().st_size for p in (home.ledger_file, index_path(home)) if p.is_file())
        free = shutil.disk_usage(home.root).free
        need = max(FREE_GB * 2**30, 2 * used)
        detail = f"{free / 2**30:.1f} GB free beside the data home"
        if free >= need:
            add("disk", OK, detail)
        else:
            add("disk", WARN, f"{detail}; keep at least {need / 2**30:.0f} GB")
        try:
            engine = make_engine(config.ledger_url)
            try:
                last = runs.last_success(engine) if migrate.is_current(engine) else {}
            finally:
                engine.dispose()
        except Exception:
            last = {}
        newest = max(last.values(), default=None)
        if newest is None:
            add("sync", WARN, "no sync has succeeded yet")
        else:
            newest = newest if newest.tzinfo else newest.replace(tzinfo=UTC)
            when = newest.isoformat()[:16].replace("T", " ")
            if utc_now() - newest > timedelta(days=SYNC_DAYS):
                add("sync", WARN, f"last success {when} UTC: is the nightly sync scheduled?")
            else:
                add("sync", OK, f"last success {when} UTC")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        add("user", WARN, "running as root: run Tessera as its own unprivileged user")
    return checks
