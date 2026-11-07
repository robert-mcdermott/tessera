"""Discovered people: candidates found in the data, and a curator's promote or dismiss."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from tessera.api.common import (
    Page,
    config_of,
    engine_of,
    envelope,
    index_of,
    page,
    refresh_index,
    split_values,
)
from tessera.core.errors import TesseraError
from tessera.evidence import store
from tessera.search.update import records_of_people

router = APIRouter(prefix="/api/v1/candidates", tags=["people"])


@router.get("", summary="People discovered in the data who await a decision, with evidence")
def list_candidates(
    request: Request,
    p: Annotated[Page, Depends(page)],
    q: str | None = Query(None, description="Free-text search over names and affiliations"),
    status: Annotated[
        list[str] | None, Query(description="candidate (default), dismissed, active")
    ] = None,
    source: Annotated[list[str] | None, Query(description="grants, publications")] = None,
    sort: str | None = Query(None, description="evidence | name | relevance"),
) -> dict[str, Any]:
    filters = {"status": split_values(status) or ["candidate"], "source": split_values(source)}
    result = index_of(request).search("candidate", q, filters, sort, p.limit, p.offset)
    items = [{**item["summary"], "highlights": item["highlights"]} for item in result.items]
    return envelope(
        items,
        total=result.total,
        limit=p.limit,
        next_cursor=p.next_cursor(result.total),
        facets=result.facets,
    )


class PersonDecisions(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)
    action: Literal["promote", "dismiss", "undo"]
    note: str | None = Field(None, max_length=2000)


@router.post(
    "/decisions",
    summary="Promote, dismiss, or undo for one or more people (each logged)",
)
def decide_people(request: Request, body: PersonDecisions) -> dict[str, Any]:
    engine, config = engine_of(request), config_of(request)
    done: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for person_id in dict.fromkeys(body.ids):
        try:
            done.append(
                store.decide_person(
                    engine, person_id, body.action, config.server.operator, body.note
                )
            )
        except TesseraError as exc:
            failed.append({"id": person_id, "detail": str(exc)})
    if len(body.ids) == 1 and failed:
        detail = failed[0]["detail"]
        raise HTTPException(
            status_code=404 if detail.startswith("No person") else 409, detail=detail
        )
    changed = [d["id"] for d in done]
    refresh_index(request, people=changed, records=records_of_people(engine, changed))
    return envelope({"decided": len(done), "results": done, "failed": failed})
