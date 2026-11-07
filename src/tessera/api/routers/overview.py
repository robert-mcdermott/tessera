"""Unified search, home-page statistics, and source health."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select

from tessera.api.common import Page, config_of, engine_of, envelope, index_of, lake_of, page
from tessera.connectors import CONNECTORS
from tessera.core.clock import isoformat, utc_now
from tessera.jobs import runs
from tessera.jobs.ingest import expected_days
from tessera.ledger.tables import ingest_run, person, source_checkpoint
from tessera.search.index import KINDS
from tessera.search.query import SearchUnavailable

router = APIRouter(prefix="/api/v1", tags=["overview"])


@router.get("/search", summary="Search everything, or one kind with facets and paging")
def search(
    request: Request,
    p: Annotated[Page, Depends(page)],
    q: str = Query("", max_length=500),
    type_: str | None = Query(
        None, alias="type", description="person | publication | grant | study"
    ),
    per_type: int = Query(5, ge=1, le=20, description="Results per kind when no type is given"),
) -> dict[str, Any]:
    index = index_of(request)
    if type_:
        if type_ not in KINDS:
            raise HTTPException(status_code=400, detail=f"type must be one of {', '.join(KINDS)}")
        result = index.search(type_, q, None, None, p.limit, p.offset)
        return envelope(
            result.items,
            total=result.total,
            limit=p.limit,
            next_cursor=p.next_cursor(result.total),
            facets=result.facets,
            query=q,
        )
    grouped = index.grouped(q, per_type)
    return envelope(
        {kind: {"total": r.total, "items": r.items} for kind, r in grouped.items()}, query=q
    )


@router.get("/stats", summary="Counts for the home page")
def stats(request: Request) -> dict[str, Any]:
    engine = engine_of(request)
    with engine.connect() as conn:
        people = dict(
            conn.execute(select(person.c.status, func.count()).group_by(person.c.status)).all()
        )
    data: dict[str, Any] = {
        "people": {
            "active": people.get("active", 0),
            "former": people.get("former", 0),
            "candidates": people.get("candidate", 0),
        },
    }
    try:
        index = index_of(request)
        counts = index.counts()
        meta = index.meta()
        years = [v for v in index.facet_values("publication", "year") if v["value"].isdigit()]
        grant_status = {v["value"]: v["count"] for v in index.facet_values("grant", "status")}
        data.update(
            {
                "publications": counts.get("publication", 0),
                "grants": {
                    "total": counts.get("grant", 0),
                    "active": grant_status.get("active", 0),
                },
                "studies": counts.get("study", 0),
                "publications_by_year": [
                    {"year": int(v["value"]), "count": v["count"]}
                    for v in sorted(years, key=lambda v: v["value"])
                ],
                "index_built_at": meta.get("built_at"),
            }
        )
    except SearchUnavailable:
        data.update(
            {
                "publications": None,
                "grants": None,
                "studies": None,
                "publications_by_year": [],
                "index_built_at": None,
            }
        )
    return envelope(data)


@router.get(
    "/sources",
    summary="Health and freshness of every source, the evidence engine, and the search index",
)
def sources(request: Request) -> dict[str, Any]:
    engine, config = engine_of(request), config_of(request)
    last = runs.last_success(engine)
    now = utc_now()
    with engine.connect() as conn:
        checkpoints = [dict(r) for r in conn.execute(select(source_checkpoint)).mappings()]
        latest = {}
        for r in conn.execute(
            select(ingest_run).order_by(ingest_run.c.started_at.desc())
        ).mappings():
            latest.setdefault(r["source"], dict(r))
    out = []
    for name, connector in CONNECTORS.items():
        finished = last.get(name)
        enabled = getattr(config.sources, name).enabled
        run = latest.get(name)
        out.append(
            {
                "name": name,
                "label": connector.label,
                "enabled": enabled,
                "expected_days": expected_days(name),
                "last_success_at": isoformat(finished) if finished else None,
                "stale": bool(
                    enabled
                    and (finished is None or now - finished > timedelta(days=expected_days(name)))
                ),
                "last_run": _run_view(run) if run else None,
                "checkpoints": [
                    {
                        "scope": c["scope"],
                        "watermark": c["watermark"],
                        "updated_at": c["updated_at"],
                    }
                    for c in checkpoints
                    if c["source"] == name
                ],
            }
        )
    evidence = latest.get("evidence")
    try:
        index_meta = index_of(request).meta()
    except SearchUnavailable:
        index_meta = {}
    return envelope(
        {
            "sources": out,
            "evidence": _run_view(evidence) if evidence else None,
            "search_index": {
                "built_at": index_meta.get("built_at"),
                "documents": {k: int(index_meta.get(f"count:{k}", 0)) for k in KINDS},
            }
            if index_meta
            else None,
            "lake": lake_of(request).stats(),
        }
    )


def _run_view(run: dict[str, Any]) -> dict[str, Any]:
    counts = run.get("counts") or {}
    report = counts.get("report") or {}
    return {
        "id": run["id"],
        "source": run["source"],
        "kind": run["kind"],
        "mode": (run.get("params") or {}).get("mode"),
        "status": run["status"],
        "started_at": run["started_at"],
        "finished_at": run["finished_at"],
        "error": run["error"],
        "total_found": report.get("total_found"),
        "payloads": report.get("payloads"),
        "failures": report.get("failure_count"),
        "notes": report.get("notes"),
    }


@router.get("/runs", summary="Recent ingest, resolve, and build runs")
def list_runs(
    request: Request, source: str | None = None, limit: int = Query(50, ge=1, le=500)
) -> dict[str, Any]:
    rows = runs.recent_runs(engine_of(request), source, limit)
    return envelope([_run_view(r) | {"counts": r["counts"]} for r in rows])
