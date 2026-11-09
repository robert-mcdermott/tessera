"""The assistant's log: every exchange, kept ``[ai] log_days`` (ADR-0008)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, insert, select
from sqlalchemy.engine import Engine

from tessera.config import AIConfig
from tessera.core.clock import utc_now
from tessera.core.ids import new_id
from tessera.ledger.tables import ai_exchange


def record(
    engine: Engine,
    config: AIConfig,
    *,
    owner: str,
    asked_by: str,
    kind: str,
    prompt_version: str,
    question: str | None,
    request: dict[str, Any],
    answer: str | None,
    citations: list[dict[str, Any]],
    usage: dict[str, int],
    status: str,
    started_at: datetime,
    error: str | None = None,
) -> str:
    """Write one exchange, and drop those older than the log keeps."""
    exchange_id = new_id("aix")
    seconds = round((utc_now() - started_at).total_seconds(), 2)
    with engine.begin() as conn:
        conn.execute(insert(ai_exchange).values(
            id=exchange_id, owner=owner, asked_by=asked_by, kind=kind, provider=config.provider,
            model=config.model, prompt_version=prompt_version, question=question,
            request=request, answer=answer, citations=citations, usage=usage, status=status,
            error=error, started_at=started_at, seconds=seconds))  # fmt: skip
        cutoff = utc_now() - timedelta(days=config.log_days)
        conn.execute(delete(ai_exchange).where(ai_exchange.c.started_at < cutoff))
    return exchange_id


def used_today(engine: Engine, owner: str, today: date | None = None) -> int:
    """Requests this owner made of the model today (UTC): questions and drafts alike, since
    each is a paid call; for the daily limit."""
    start = datetime.combine(today or utc_now().date(), datetime.min.time()).replace(
        tzinfo=utc_now().tzinfo
    )
    with engine.connect() as conn:
        return int(conn.execute(
            select(func.count()).select_from(ai_exchange).where(
                ai_exchange.c.owner == owner,
                ai_exchange.c.status != "limited", ai_exchange.c.started_at >= start)
        ).scalar() or 0)  # fmt: skip


def recent(engine: Engine, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    with engine.connect() as conn:
        total = int(conn.execute(select(func.count()).select_from(ai_exchange)).scalar() or 0)
        rows = [dict(r) for r in conn.execute(
            select(ai_exchange).order_by(ai_exchange.c.started_at.desc())
            .limit(limit).offset(offset)).mappings()]  # fmt: skip
    return rows, total


def usage_by_day(engine: Engine, days: int = 30) -> list[dict[str, Any]]:
    since = utc_now() - timedelta(days=days)
    out: dict[str, dict[str, Any]] = {}
    with engine.connect() as conn:
        for started, status, usage in conn.execute(
            select(ai_exchange.c.started_at, ai_exchange.c.status, ai_exchange.c.usage)
            .where(ai_exchange.c.started_at >= since)
        ):  # fmt: skip
            day = out.setdefault(started.date().isoformat(), {
                "day": started.date().isoformat(), "exchanges": 0, "declined": 0, "failed": 0,
                "input_tokens": 0, "output_tokens": 0})  # fmt: skip
            day["exchanges"] += 1
            day["declined"] += status == "declined"
            day["failed"] += status == "failed"
            day["input_tokens"] += int((usage or {}).get("input", 0))
            day["output_tokens"] += int((usage or {}).get("output", 0))
    return sorted(out.values(), key=lambda d: d["day"])


__all__ = ["recent", "record", "usage_by_day", "used_today"]
