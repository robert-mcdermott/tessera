"""Run records and incremental checkpoints, stored in the ledger."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, desc, func, insert, select, update
from sqlalchemy.engine import Engine

from tessera.core.clock import utc_now
from tessera.core.ids import new_id
from tessera.ledger.tables import ingest_run, source_checkpoint


def start_run(
    engine: Engine, source: str, kind: str, params: dict[str, Any], run_id: str | None = None
) -> str:
    run_id = run_id or new_id("run")
    with engine.begin() as conn:
        conn.execute(
            insert(ingest_run).values(
                id=run_id,
                source=source,
                kind=kind,
                status="running",
                started_at=utc_now(),
                params=params,
                counts={},
            )
        )
    return run_id


def finish_run(
    engine: Engine, run_id: str, status: str, counts: dict[str, Any], error: str | None = None
) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(ingest_run)
            .where(ingest_run.c.id == run_id)
            .values(status=status, finished_at=utc_now(), counts=counts, error=error)
        )


def mark_interrupted(engine: Engine) -> int:
    """Mark runs left 'running' by a crashed process as failed. Call while holding the run lock."""
    with engine.begin() as conn:
        result = conn.execute(
            update(ingest_run)
            .where(ingest_run.c.status == "running")
            .values(status="failed", finished_at=utc_now(), error="interrupted")
        )
        return result.rowcount or 0


def get_checkpoint(engine: Engine, source: str, scope: str) -> str | None:
    if not scope:
        return None
    with engine.connect() as conn:
        return conn.execute(
            select(source_checkpoint.c.watermark).where(
                and_(source_checkpoint.c.source == source, source_checkpoint.c.scope == scope)
            )
        ).scalar()


def set_checkpoint(engine: Engine, source: str, scope: str, watermark: str, run_id: str) -> None:
    now = utc_now()
    with engine.begin() as conn:
        updated = conn.execute(
            update(source_checkpoint)
            .where(and_(source_checkpoint.c.source == source, source_checkpoint.c.scope == scope))
            .values(watermark=watermark, run_id=run_id, updated_at=now)
        )
        if not updated.rowcount:
            conn.execute(
                insert(source_checkpoint).values(
                    source=source, scope=scope, watermark=watermark, run_id=run_id, updated_at=now
                )
            )


def get_run(engine: Engine, run_id: str) -> dict[str, Any] | None:
    with engine.connect() as conn:
        row = conn.execute(select(ingest_run).where(ingest_run.c.id == run_id)).mappings().first()
    return dict(row) if row else None


def recent_runs(engine: Engine, source: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    query = select(ingest_run).order_by(desc(ingest_run.c.started_at)).limit(limit)
    if source:
        query = query.where(ingest_run.c.source == source)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(query).mappings()]


def last_success(engine: Engine) -> dict[str, datetime]:
    """The finish time of each source's latest successful fetch (replays excluded)."""
    query = (
        select(ingest_run.c.source, func.max(ingest_run.c.finished_at))
        .where(and_(ingest_run.c.status == "succeeded", ingest_run.c.kind != "replay"))
        .group_by(ingest_run.c.source)
    )
    with engine.connect() as conn:
        rows = conn.execute(query).all()
    return {source: value for source, value in rows if value is not None}
