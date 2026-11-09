"""Assertions, decisions, and the review queue."""

from __future__ import annotations

from collections import Counter
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from tessera.api.access import principal_of, require_curate
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
    refresh_index,
    split_values,
)
from tessera.core.errors import TesseraError
from tessera.evidence import store
from tessera.ledger.tables import assertion, curation_event, person, person_event, unit

router = APIRouter(prefix="/api/v1", tags=["curation"])

CONFLICTS = frozenset(
    {
        "slot_conflict",
        "middle_conflict",
        "given_conflict",
        "orcid_conflict",
        "orcid_rejected",
        "orcid_inconsistent",
    }
)


def _label(engine, ref: str, index) -> str | None:  # type: ignore[no-untyped-def]
    if ref.startswith("per_"):
        return store.display_names(engine, [ref]).get(ref)
    if ref.startswith("unt_"):
        with engine.connect() as conn:
            return conn.execute(select(unit.c.name).where(unit.c.id == ref)).scalar()
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
    view["can_decide"] = _may_decide(
        request, {"subject_id": row["subject_id"], "object_id": row["object_id"]}
    )
    return envelope(view)


class Decision(BaseModel):
    action: Literal["accept", "reject", "defer", "undo"]
    note: str | None = Field(None, max_length=2000)


def _may_decide(request: Request, row: dict[str, Any]) -> bool:
    """Whether the principal may decide this link: one about a person needs that person (both,
    for a pair of records) in their scope; a link between records needs a curator for all."""
    principal = principal_of(request)
    if row["object_id"].startswith("unt_"):  # a paper attributed to a group
        return principal.edits_group(engine_of(request), row["object_id"])
    people = [ref for ref in (row["subject_id"], row["object_id"]) if ref.startswith("per_")]
    if not people:
        return principal.curates_all
    engine = engine_of(request)
    return all(principal.can_curate(engine, ref) for ref in people)


def _assertion_row(request: Request, assertion_id: str) -> dict[str, Any] | None:
    with engine_of(request).connect() as conn:
        row = (
            conn.execute(
                select(assertion.c.subject_id, assertion.c.object_id).where(
                    assertion.c.id == assertion_id
                )
            )
            .mappings()
            .first()
        )
    return dict(row) if row else None


@router.post(
    "/assertions/{assertion_id}/decision",
    summary="Accept, reject, defer, or undo; recorded as a curation event",
)
def decide(request: Request, assertion_id: str, body: Decision) -> dict[str, Any]:
    engine = engine_of(request)
    row = _assertion_row(request, assertion_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such assertion.")
    if not _may_decide(request, row):
        raise HTTPException(
            status_code=403, detail="You may decide only for the people and groups in your scope."
        )
    try:
        store.decide(engine, assertion_id, body.action, principal_of(request).name, body.note)
    except TesseraError as exc:
        message = str(exc)
        if message.startswith("No assertion"):
            raise HTTPException(status_code=404, detail="No such assertion.") from None
        raise HTTPException(status_code=409, detail=message) from None
    _refresh_for(request, [assertion_id])
    return get_assertion(request, assertion_id)


def _refresh_for(request: Request, assertion_ids: list[str]) -> None:
    """Update the search documents of the people and records these assertions link."""
    if not assertion_ids:
        return
    with engine_of(request).connect() as conn:
        rows = conn.execute(
            select(assertion.c.subject_id, assertion.c.object_id).where(
                assertion.c.id.in_(assertion_ids)
            )
        ).all()
    refresh_index(
        request,
        people={r[0] for r in rows if r[0].startswith("per_")},
        records={r[1] for r in rows},
    )


class BatchDecision(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)
    action: Literal["accept", "reject", "defer", "undo"]
    note: str | None = Field(None, max_length=2000)


@router.post(
    "/assertions/decisions",
    summary="Apply one decision to several assertions (each logged; used for bulk undo)",
)
def decide_many(request: Request, body: BatchDecision) -> dict[str, Any]:
    engine = engine_of(request)
    actor = principal_of(request).name
    done: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for assertion_id in dict.fromkeys(body.ids):
        row = _assertion_row(request, assertion_id)
        if row is not None and not _may_decide(request, row):
            failed.append({"id": assertion_id, "detail": "Not in your scope."})
            continue
        try:
            done.append(store.decide(engine, assertion_id, body.action, actor, body.note))
        except TesseraError as exc:
            failed.append({"id": assertion_id, "detail": str(exc)})
    _refresh_for(request, [d["id"] for d in done])
    return envelope({"decided": len(done), "results": done, "failed": failed})


def _directory(engine) -> dict[str, str]:  # type: ignore[no-untyped-def]
    with engine.connect() as conn:
        return dict(
            conn.execute(
                select(person.c.id, person.c.display_name).where(
                    person.c.status.in_(("active", "former"))
                )
            ).all()
        )


def _count_below(engine, floor: float, person_id: str | None, scope: set[str] | None = None) -> int:  # type: ignore[no-untyped-def]
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
    elif scope is not None:
        query = query.where(assertion.c.subject_id.in_(scope))
    with engine.connect() as conn:
        return conn.execute(query).scalar() or 0


def _priority(row: dict[str, Any], row_band: str | None, names: dict[str, str]) -> tuple:
    features = {e["feature"] for e in row["evidence"] or []}
    score = -(row["score"] or 0)
    last = row["id"]  # ties in a fixed order, whatever order the database returned them in
    if row_band == "strong":  # quick confirmations first, grouped by person
        return (0, names.get(row["subject_id"], ""), score, last)
    if row_band == "possible":
        if features & CONFLICTS:
            return (1, score, last)
        if "name_ambiguity" in features:
            return (2, score, last)
        return (3, score, last)
    return (4, score, last)


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
    # Each reviewer sees the people they may decide for (a researcher: themselves).
    scope = principal_of(request).curated_people(engine)
    if scope is not None:
        names = {pid: name for pid, name in names.items() if pid in scope}
        if person_id is None and len(scope) == 1:
            person_id = next(iter(scope))
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
        counts["unlikely"] = _count_below(engine, floor, person_id, scope)
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
    actor = require_curate(request, body.person_id).name
    rows = [
        r
        for r in store.assertions_for(
            engine, predicate="authored", statuses=("suggested",), subject_id=body.person_id
        )
        if band(r["score"], config) == body.band
    ]
    for row in rows:
        store.decide(engine, row["id"], body.action, actor, body.note)
    refresh_index(
        request, people=[body.person_id] if rows else [], records={r["object_id"] for r in rows}
    )
    return envelope(
        {
            "decided": len(rows),
            "ids": [r["id"] for r in rows],
            "status": "accepted" if body.action == "accept" else "rejected",
        }
    )


@router.get("/decisions", summary="Recent decisions about links and people, newest first")
def recent_decisions(
    request: Request,
    p: Annotated[Page, Depends(page)],
) -> dict[str, Any]:
    """The curation log across the organization: what was decided, by whom, and when."""
    engine, index = engine_of(request), index_of(request)
    window = p.offset + p.limit
    with engine.connect() as conn:
        link_events = [
            dict(r)
            for r in conn.execute(
                select(
                    curation_event,
                    assertion.c.subject_id,
                    assertion.c.predicate,
                    assertion.c.object_id,
                    assertion.c.status.label("current_status"),
                )
                .join(assertion, assertion.c.id == curation_event.c.assertion_id)
                .order_by(curation_event.c.at.desc(), curation_event.c.id.desc())
                .limit(window)
            ).mappings()
        ]
        people_events = [
            dict(r)
            for r in conn.execute(
                select(person_event, person.c.display_name, person.c.status.label("current_status"))
                .join(person, person.c.id == person_event.c.person_id)
                .order_by(person_event.c.at.desc(), person_event.c.id.desc())
                .limit(window)
            ).mappings()
        ]
        total = (conn.execute(select(func.count()).select_from(curation_event)).scalar() or 0) + (
            conn.execute(select(func.count()).select_from(person_event)).scalar() or 0
        )
    merged = sorted(
        [("link", e) for e in link_events] + [("person", e) for e in people_events],
        key=lambda item: (item[1]["at"], item[1]["id"]),
        reverse=True,
    )[p.offset : window]
    subjects = {e["subject_id"] for kind, e in merged if kind == "link"}
    survivors = store.display_names(
        engine,
        {
            e["detail"]["survivor_id"]
            for kind, e in merged
            if kind == "person" and e["action"] in ("merge", "unmerge") and e.get("detail")
        },
    )
    names = store.display_names(engine, subjects)
    try:
        titles = index.summaries([e["object_id"] for kind, e in merged if kind == "link"])
    except Exception:  # the index is optional for this log
        titles = {}
    items: list[dict[str, Any]] = []
    for kind, e in merged:
        base = {
            "kind": kind,
            "action": e["action"],
            "from_status": e["from_status"],
            "to_status": e["to_status"],
            "actor": e["actor"],
            "note": e["note"],
            "at": e["at"],
        }
        if kind == "link":
            summary = titles.get(e["object_id"], {})
            base |= {
                "assertion_id": e["assertion_id"],
                "predicate": e["predicate"],
                "status": e["current_status"],
                "subject": {"id": e["subject_id"], "label": names.get(e["subject_id"])},
                "object": {"id": e["object_id"], "label": summary.get("title")},
            }
        else:
            base |= {
                "status": e["current_status"],
                "subject": {"id": e["person_id"], "label": e["display_name"]},
            }
            if e["action"] == "identifier":
                base["identifier"] = e["detail"]
            elif e["action"] in ("merge", "unmerge") and e["detail"]:
                survivor = e["detail"].get("survivor_id")
                base["merge"] = {
                    "id": e["detail"].get("merge_id"),
                    "survivor": {"id": survivor, "label": survivors.get(survivor)},
                }
        items.append(base)
    return envelope(items, total=total, limit=p.limit, next_cursor=p.next_cursor(total))
