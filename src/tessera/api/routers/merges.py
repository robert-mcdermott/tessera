"""Possible duplicates, merges, and unmerges (REC-07)."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from tessera.api.common import (
    Page,
    band,
    config_of,
    engine_of,
    envelope,
    page,
    refresh_index,
    split_values,
)
from tessera.core.errors import TesseraError
from tessera.evidence.memberships import is_current
from tessera.evidence.merge import MergeError, merge_people, unmerge
from tessera.ledger.tables import assertion, person, person_identifier, unit
from tessera.search.update import records_of_people

router = APIRouter(prefix="/api/v1", tags=["people"])

STATUSES = ("suggested", "deferred", "rejected", "accepted")


def _people_summaries(engine, ids: set[str], config) -> dict[str, dict[str, Any]]:  # type: ignore[no-untyped-def]
    """What a curator compares to decide whether two records are one person."""
    if not ids:
        return {}
    today = date.today()
    with engine.connect() as conn:
        rows = {
            r["id"]: dict(r)
            for r in conn.execute(select(person).where(person.c.id.in_(ids))).mappings()
        }
        units = {r.id: r.name for r in conn.execute(select(unit.c.id, unit.c.name))}
        identifiers = defaultdict(list)
        for r in conn.execute(
            select(person_identifier).where(
                person_identifier.c.person_id.in_(ids), person_identifier.c.status != "rejected"
            )
        ).mappings():
            identifiers[r["person_id"]].append(
                {"scheme": r["scheme"], "value": r["value"], "status": r["status"]}
            )
        links = conn.execute(
            select(
                assertion.c.subject_id,
                assertion.c.predicate,
                assertion.c.object_id,
                assertion.c.status,
                assertion.c.score,
                assertion.c.qualifiers,
            ).where(
                assertion.c.subject_id.in_(ids),
                assertion.c.predicate.in_(("authored", "investigator_on", "member_of")),
                assertion.c.status != "rejected",
                assertion.c.reported.is_(True),
            )
        ).mappings()
        papers: dict[str, Counter] = defaultdict(Counter)
        grants: Counter = Counter()
        memberships: dict[str, list[str]] = defaultdict(list)
        for r in links:
            if r["predicate"] == "authored":
                if r["status"] == "accepted":
                    papers[r["subject_id"]]["accepted"] += 1
                elif (r["score"] or 0) >= config.evidence.strong_threshold:
                    papers[r["subject_id"]]["strong"] += 1
            elif r["predicate"] == "investigator_on":
                grants[r["subject_id"]] += 1
            elif is_current(r["qualifiers"], today):
                memberships[r["subject_id"]].append(units.get(r["object_id"], r["object_id"]))
    return {
        pid: {
            "id": pid,
            "name": row["display_name"],
            "status": row["status"],
            "created_at": row["created_at"],
            "units": sorted(memberships[pid]),
            "identifiers": sorted(identifiers[pid], key=lambda i: (i["scheme"], i["value"])),
            "papers": {"accepted": papers[pid]["accepted"], "strong": papers[pid]["strong"]},
            "grants": grants[pid],
        }
        for pid, row in rows.items()
    }


@router.get("/duplicates", summary="Possible duplicate records of one person, with evidence")
def list_duplicates(
    request: Request,
    p: Annotated[Page, Depends(page)],
    status: Annotated[
        list[str] | None, Query(description="suggested (default), deferred, rejected, accepted")
    ] = None,
) -> dict[str, Any]:
    engine, config = engine_of(request), config_of(request)
    wanted = [s for s in (split_values(status) or ["suggested"]) if s in STATUSES]
    with engine.connect() as conn:
        live = select(person.c.id).where(person.c.status != "merged")
        where = [
            assertion.c.predicate == "same_as",
            assertion.c.status.in_(wanted),
        ]
        if "accepted" not in wanted:  # pairs already merged are history, not work
            where += [assertion.c.subject_id.in_(live), assertion.c.object_id.in_(live)]
        total = conn.execute(select(func.count()).select_from(assertion).where(*where)).scalar()
        rows = [
            dict(r)
            for r in conn.execute(
                select(assertion)
                .where(*where)
                .order_by(assertion.c.score.desc().nulls_last(), assertion.c.id)
                .limit(p.limit)
                .offset(p.offset)
            ).mappings()
        ]
        counts = dict(
            conn.execute(
                select(assertion.c.status, func.count())
                .where(
                    assertion.c.predicate == "same_as",
                    assertion.c.subject_id.in_(live),
                    assertion.c.object_id.in_(live),
                )
                .group_by(assertion.c.status)
            ).all()
        )
    people = _people_summaries(
        engine, {r["subject_id"] for r in rows} | {r["object_id"] for r in rows}, config
    )
    items = [
        {
            "id": r["id"],
            "status": r["status"],
            "score": r["score"],
            "band": band(r["score"], config) if r["score"] is not None else None,
            "evidence": r["evidence"],
            "method": r["method"],
            "decided_by": r["decided_by"],
            "decided_at": r["decided_at"],
            "note": r["decision_note"],
            # The record to fold in, and the one to keep (the suggestion; either can be kept).
            "merge": people.get(r["subject_id"]),
            "into": people.get(r["object_id"]),
        }
        for r in rows
    ]
    return envelope(
        items,
        total=total,
        limit=p.limit,
        next_cursor=p.next_cursor(total or 0),
        facets={"status": [{"value": k, "count": v} for k, v in sorted(counts.items())]},
    )


class MergeRequest(BaseModel):
    merge: str = Field(description="The record to fold in (it becomes 'merged')")
    into: str = Field(description="The record to keep")
    note: str | None = Field(None, max_length=2000)


@router.post("/merges", summary="Merge two records of one person (reversible, logged)")
def create_merge(request: Request, body: MergeRequest) -> dict[str, Any]:
    """Links, names, and identifiers move to the record kept. Where both records decided the
    same link differently, the more recent decision holds and the result lists the conflict."""
    engine, config = engine_of(request), config_of(request)
    try:
        result = merge_people(engine, body.merge, body.into, config.server.operator, body.note)
    except MergeError as exc:
        status = 404 if str(exc).startswith("No person") else 409
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    refresh_index(
        request,
        people=[body.merge, body.into],
        records=records_of_people(engine, [body.into]),
    )
    return envelope(result)


class UndoRequest(BaseModel):
    note: str | None = Field(None, max_length=2000)


@router.post("/merges/{merge_id}/undo", summary="Undo a merge exactly")
def undo_merge(request: Request, merge_id: str, body: UndoRequest | None = None) -> dict[str, Any]:
    engine, config = engine_of(request), config_of(request)
    try:
        result = unmerge(engine, merge_id, config.server.operator, body.note if body else None)
    except MergeError as exc:
        status = 404 if str(exc).startswith("No merge") else 409
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except TesseraError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    people = [result["merged_id"], result["survivor_id"]]
    refresh_index(request, people=people, records=records_of_people(engine, people))
    return envelope(result)
