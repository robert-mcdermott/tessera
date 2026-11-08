"""Operational status and alerts (PLT-12).

``status`` gathers what an administrator needs to see the installation is healthy: each
source's runs over the last 30 days (successes, failures, durations, records), recent runs,
freshness, the sizes of the ledger, the lake, and the other parts of the data home, the last
backup, and whether a data job is running now.

``issues`` lists what deserves an alert: failed or partial runs, sources that have gone stale,
and backups older than ``[alerts] backup_days``. ``alert`` emails them to ``[alerts] email``
through ``[mail]``, once per new set of issues (and again after a week if they persist).
"""

from __future__ import annotations

import contextlib
import hashlib
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from statistics import median
from typing import Any

from sqlalchemy import insert, select, text, update
from sqlalchemy.engine import Engine

from tessera.config import Config
from tessera.connectors import CONNECTORS
from tessera.core.clock import isoformat, utc_now
from tessera.home import DataHome
from tessera.jobs.ingest import expected_days
from tessera.jobs.lock import LOCK_NAME
from tessera.lake.manifest import read_manifest, table_dir
from tessera.lake.schemas import TABLES
from tessera.ledger.tables import ingest_run, setting
from tessera.mail import message, send
from tessera.ops.backup import last_backup

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

WINDOW_DAYS = 30
LAST_ALERT = "operations.last_alert"
REPEAT_AFTER = timedelta(days=7)


def _bytes(root: Path) -> int:
    if not root.exists():
        return 0
    if root.is_file():
        return root.stat().st_size
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def job_running(home: DataHome) -> bool:
    """Whether a sync or another data job holds the data home's lock now."""
    path = home.root / LOCK_NAME
    if fcntl is None or not path.exists():
        return False
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return False


def _ledger_bytes(engine: Engine, home: DataHome) -> int | None:
    if engine.dialect.name == "sqlite":
        database = engine.url.database or ""
        return sum(_bytes(Path(database + suffix)) for suffix in ("", "-wal", "-shm"))
    with contextlib.suppress(Exception), engine.connect() as conn:
        return int(conn.execute(text("SELECT pg_database_size(current_database())")).scalar())
    return None


def _duration(run: dict[str, Any]) -> float | None:
    if run["finished_at"] and run["started_at"]:
        return round((run["finished_at"] - run["started_at"]).total_seconds(), 2)
    return None


def _records(run: dict[str, Any]) -> int | None:
    counts = run.get("counts") or {}
    report = counts.get("report") or {}
    for key in ("rows_written", "records", "total_found"):
        if isinstance(counts.get(key), int):
            return counts[key]
        if isinstance(report.get(key), int):
            return report[key]
    return None


def status(config: Config, home: DataHome, engine: Engine) -> dict[str, Any]:
    now = utc_now()
    since = now - timedelta(days=WINDOW_DAYS)
    with engine.connect() as conn:
        rows = [
            dict(r) for r in conn.execute(
                select(ingest_run).where(ingest_run.c.started_at >= since)
                .order_by(ingest_run.c.started_at.desc())
            ).mappings()
        ]  # fmt: skip
        latest_success = {
            r.source: r.finished_at
            for r in conn.execute(
                select(ingest_run.c.source, ingest_run.c.finished_at)
                .where(ingest_run.c.status == "succeeded", ingest_run.c.kind != "replay")
                .order_by(ingest_run.c.finished_at)
            )
        }  # fmt: skip
    by_source: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_source.setdefault(r["source"], []).append(r)
    sources = []
    for name in [*CONNECTORS, *sorted(set(by_source) - set(CONNECTORS))]:
        runs = by_source.get(name, [])
        durations = [d for d in (_duration(r) for r in runs) if d is not None]
        failures = [r for r in runs if r["status"] in ("failed", "partial")]
        connector = CONNECTORS.get(name)
        enabled = getattr(config.sources, name).enabled if connector else True
        finished = latest_success.get(name)
        stale = bool(connector and enabled and (
            finished is None or now - finished > timedelta(days=expected_days(name))))  # fmt: skip
        sources.append(
            {
                "source": name,
                "label": connector.label if connector else name.capitalize(),
                "kind": "source" if connector else "job",
                "enabled": enabled,
                "runs": len(runs),
                "failures": len(failures),
                "last_status": runs[0]["status"] if runs else None,
                "last_run_at": isoformat(runs[0]["started_at"]) if runs else None,
                "last_success_at": isoformat(finished) if finished else None,
                "stale": stale,
                "expected_days": expected_days(name) if connector else None,
                "median_seconds": round(median(durations), 2) if durations else None,
                "durations": [
                    d for d in (_duration(r) for r in reversed(runs[:20])) if d is not None
                ],
                "last_error": failures[0]["error"] if failures else None,
            }
        )
    lake_tables = []
    for name, spec in TABLES.items():
        manifest = read_manifest(home.lake, name, spec.version)
        lake_tables.append({"table": name, "files": len(manifest.files), "rows": manifest.rows,
                            "bytes": _bytes(table_dir(home.lake, name))})  # fmt: skip
    backup = last_backup(engine)
    return {
        "generated_at": isoformat(now),
        "job_running": job_running(home),
        "sources": sources,
        "recent": [
            {
                "id": r["id"],
                "source": r["source"],
                "kind": r["kind"],
                "status": r["status"],
                "started_at": isoformat(r["started_at"]),
                "seconds": _duration(r),
                "records": _records(r),
                "error": r["error"],
            }
            for r in rows[:50]
        ],
        "sizes": {
            "ledger": _ledger_bytes(engine, home),
            "lake": _bytes(home.lake),
            "raw": _bytes(home.raw),
            "derived": _bytes(home.derived),
            "snapshots": _bytes(home.root / "snapshots"),
            "backups": _bytes(home.root / "backups"),
            "lake_tables": lake_tables,
        },
        "backup": backup,
        "alerts": {
            "email": list(config.alerts.email),
            "mail": bool(config.mail.host),
            "backup_days": config.alerts.backup_days,
        },
        "issues": issues(config, home, engine),
    }


def issues(config: Config, home: DataHome, engine: Engine) -> list[dict[str, Any]]:
    """What deserves attention now, each with a stable key so repeats can be recognized."""
    now = utc_now()
    out: list[dict[str, Any]] = []
    with engine.connect() as conn:
        since = now - timedelta(days=2)
        failed = conn.execute(
            select(ingest_run.c.id, ingest_run.c.source, ingest_run.c.status, ingest_run.c.error,
                   ingest_run.c.started_at)
            .where(ingest_run.c.status.in_(("failed", "partial")),
                   ingest_run.c.started_at >= since)
            .order_by(ingest_run.c.started_at.desc())
        ).all()  # fmt: skip
        latest_success = dict(
            conn.execute(
                select(ingest_run.c.source, ingest_run.c.finished_at)
                .where(ingest_run.c.status == "succeeded", ingest_run.c.kind != "replay")
                .order_by(ingest_run.c.finished_at)
            ).all()
        )  # fmt: skip
    for r in failed:
        out.append({
            "key": f"run:{r.id}", "kind": "failure",
            "text": f"{r.source} run {r.status} at {isoformat(r.started_at)}: "
                    f"{(r.error or 'no error recorded').splitlines()[0][:300]}",
        })  # fmt: skip
    for name, connector in CONNECTORS.items():
        if not getattr(config.sources, name).enabled:
            continue
        finished = latest_success.get(name)
        if finished is None or now - finished > timedelta(days=expected_days(name)):
            when = f"last fetched {isoformat(finished)}" if finished else "never fetched"
            out.append({"key": f"stale:{name}", "kind": "stale",
                        "text": f"{connector.label} is stale: {when} (expected every "
                                f"{expected_days(name)} days)."})  # fmt: skip
    if config.alerts.backup_days:
        backup = last_backup(engine)
        limit = timedelta(days=config.alerts.backup_days)
        taken = datetime.fromisoformat(backup["at"]) if backup else None
        if taken is None or now - taken > limit:
            when = f"the last was {backup['at']}" if backup else "there is none"
            key = f"backup:{(taken or now).date().isoformat()}"
            text_ = (f"No backup in the last {config.alerts.backup_days} days ({when}). "
                     "Run `tessera backup`.")  # fmt: skip
            out.append({"key": key, "kind": "backup", "text": text_})
    return out


def alert(
    config: Config, engine: Engine, home: DataHome, *, send_mail: bool = True, force: bool = False
) -> tuple[list[dict[str, Any]], EmailMessage | None, bool]:
    """(issues, the message, whether it was sent). Sends only when the set of issues is new,
    or a week has passed since the same set was sent, unless ``force``."""
    found = issues(config, home, engine)
    if not found or not config.alerts.email:
        return found, None, False
    signature = hashlib.sha256("\n".join(sorted(i["key"] for i in found)).encode()).hexdigest()
    now = utc_now()
    with engine.connect() as conn:
        last = conn.execute(select(setting.c.value).where(setting.c.key == LAST_ALERT)).scalar()
    repeat = bool(last and last.get("signature") == signature
                  and now - datetime.fromisoformat(last["at"]) < REPEAT_AFTER)  # fmt: skip
    lines = [f"Tessera at {config.organization.name} needs attention:", ""]
    lines += [f"- {i['text']}" for i in found]
    if config.mail.base_url:
        lines += ["", f"Admin → Operations: {config.mail.base_url.rstrip('/')}/admin/operations"]
    subject = f"Tessera: {len(found)} {'issue' if len(found) == 1 else 'issues'} need attention"
    messages = [message(config.mail, to, subject, "\n".join(lines) + "\n")
                for to in config.alerts.email]  # fmt: skip
    if repeat and not force:
        return found, messages[0], False
    if not send_mail or not config.mail.host:
        return found, messages[0], False
    send(config.mail, messages)
    value = {"at": isoformat(now), "signature": signature, "issues": len(found)}
    with engine.begin() as conn:
        exists = conn.execute(select(setting.c.key).where(setting.c.key == LAST_ALERT)).first()
        values = {"value": value, "updated_by": "tessera alerts", "updated_at": now}
        if exists:
            conn.execute(update(setting).where(setting.c.key == LAST_ALERT).values(**values))
        else:
            conn.execute(insert(setting).values(key=LAST_ALERT, **values))
    return found, messages[0], True


__all__ = ["alert", "issues", "job_running", "status"]
