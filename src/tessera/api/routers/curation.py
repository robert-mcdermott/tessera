"""Assertions, decisions, and the review queue."""

from __future__ import annotations

from collections import Counter
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from tessera.api.common import (
    Page,
    assertion_view,
    band,
    config_of,
    engine_of,
    envelope,
    index_of,
    lake_of,
    page,
    publication_summaries,
    split_values,
)
from tessera.core.errors import TesseraError
from tessera.evidence import store
from tessera.ledger.tables import assertion, curation_event, person

router = APIRouter(prefix="/api/v1", tags=["curation"])

CONFLICTS = frozenset({"slot_conflict", "middle_conflict", "given_conflict", "orcid_conflict"})


def _label(engine, ref: str, index) -> str | None:  # type: ignore[no-untyped-def]
    if ref.startswith("per_"):
        return store.display_names(engine, [ref]).get(ref)
    try:
        summary = index.summaries([ref]).get(ref)
    except Exception:
        summary = None
    return summary.get("title") if summary else None


@router.get("/assertions/{assertion_id}", summary="An assertion with its evidence and history")
def get_assertion(request: Request, assertion_id: str) -> dict[str, Any]:
    engine, config, index = engine_of(request), config_of(request), index_of(request)
    with engine.connect() as conn:
        row = (
            conn.execute(select(assertion).where(assertion.c.id == assertion_id)).mappings().first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="No such assertion.")
        history = [
            dict(r)
            for r in conn.execute(
                select(curation_event)
                .where(curation_event.c.assertion_id == assertion_id)
                .order_by(curation_event.c.id)
            ).mappings()
        ]
    view = assertion_view(dict(row), config)
    view["subject"]["label"] = _label(engine, row["subject_id"], index)
    view["object"]["label"] = _label(engine, row["object_id"], index)
    view["history"] = history
    return envelope(view)


class Decision(BaseModel):
    action: Literal["accept", "reject", "defer", "undo"]
    note: str | None = Field(None, max_length=2000)


@router.post(
    "/assertions/{assertion_id}/decision",
    summary="Accept, reject, defer, or undo; recorded as a curation event",
)
def decide(request: Request, assertion_id: str, body: Decision) -> dict[str, Any]:
    engine, config = engine_of(request), config_of(request)
    try:
        store.decide(engine, assertion_id, body.action, config.server.operator, body.note)
    except TesseraError as exc:
        message = str(exc)
        if message.startswith("No assertion"):
            raise HTTPException(status_code=404, detail="No such assertion.") from None
        raise HTTPException(status_code=409, detail=message) from None
    return get_assertion(request, assertion_id)


def _directory(engine) -> dict[str, str]:  # type: ignore[no-untyped-def]
    with engine.connect() as conn:
        return dict(
            conn.execute(
                select(person.c.id, person.c.display_name).where(
                    person.c.status.in_(("active", "former"))
                )
            ).all()
        )


def _count_below(engine, floor: float, person_id: str | None) -> int:  # type: ignore[no-untyped-def]
    query = (
        select(func.count())
        .select_from(assertion.join(person, person.c.id == assertion.c.subject_id))
        .where(
            assertion.c.predicate == "authored",
            assertion.c.status == "suggested",
            assertion.c.reported.is_(True),
            assertion.c.score < floor,
            person.c.status.in_(("active", "former")),
        )
    )
    if person_id:
        query = query.where(assertion.c.subject_id == person_id)
    with engine.connect() as conn:
        return conn.execute(query).scalar() or 0


def _priority(row: dict[str, Any], row_band: str | None, names: dict[str, str]) -> tuple:
    features = {e["feature"] for e in row["evidence"] or []}
    score = -(row["score"] or 0)
    if row_band == "strong":  # quick confirmations first, grouped by person
        return (0, names.get(row["subject_id"], ""), score)
    if row_band == "possible":
        if features & CONFLICTS:
            return (1, score)
        if "name_ambiguity" in features:
            return (2, score)
        return (3, score)
    return (4, score)


@router.get("/review", summary="The review queue of suggested authorships, in priority order")
def review_queue(
    request: Request,
    p: Annotated[Page, Depends(page)],
    band_: Annotated[
        list[str] | None, Query(alias="band", description="strong, possible, unlikely")
    ] = None,
    person_id: str | None = Query(None, alias="person"),
) -> dict[str, Any]:
    engine, config = engine_of(request), config_of(request)
    bands = split_values(band_) or ["strong", "possible"]
    names = _directory(engine)
    floor = None if "unlikely" in bands else config.evidence.review_threshold
    rows = [
        r
        for r in store.assertions_for(
            engine,
            predicate="authored",
            statuses=("suggested",),
            subject_id=person_id,
            min_score=floor,
        )
        if r["subject_id"] in names
    ]
    counts = Counter(band(r["score"], config) for r in rows)
    if floor is not None:  # count the unlikely band without loading it
        counts["unlikely"] = _count_below(engine, floor, person_id)
    chosen = [r for r in rows if band(r["score"], config) in bands]
    chosen.sort(key=lambda r: _priority(r, band(r["score"], config), names))
    window = chosen[p.offset : p.offset + p.limit]
    summaries = publication_summaries(
        index_of(request), lake_of(request), [r["object_id"].removeprefix("pub:") for r in window]
    )
    items = [
        {
            "assertion": assertion_view(r, config),
            "person": {"id": r["subject_id"], "name": names[r["subject_id"]]},
            "publication": summaries.get(
                r["object_id"].removeprefix("pub:"), {"pmid": r["object_id"].removeprefix("pub:")}
            ),
        }
        for r in window
    ]
    return envelope(
        items,
        total=len(chosen),
        limit=p.limit,
        next_cursor=p.next_cursor(len(chosen)),
        bands=dict(counts),
        prefetch=min(5, max(0, len(chosen) - p.offset - p.limit)),
    )


class BulkDecision(BaseModel):
    person_id: str
    band: Literal["strong", "possible", "unlikely"]
    action: Literal["accept", "reject"]
    note: str | None = Field(None, max_length=2000)


@router.post(
    "/review/bulk",
    summary="Decide every open suggestion in one band for one person (logged per item)",
)
def bulk_decide(request: Request, body: BulkDecision) -> dict[str, Any]:
    engine, config = engine_of(request), config_of(request)
    if body.person_id not in _directory(engine):
        raise HTTPException(status_code=404, detail="No such person.")
    rows = [
        r
        for r in store.assertions_for(
            engine, predicate="authored", statuses=("suggested",), subject_id=body.person_id
        )
        if band(r["score"], config) == body.band
    ]
    for row in rows:
        store.decide(engine, row["id"], body.action, config.server.operator, body.note)
    return envelope(
        {
            "decided": len(rows),
            "ids": [r["id"] for r in rows],
            "status": "accepted" if body.action == "accept" else "rejected",
        }
    )
