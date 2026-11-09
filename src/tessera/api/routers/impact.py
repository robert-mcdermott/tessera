"""Grant-to-impact threads (INT-08): from a grant, a paper, or a person."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from tessera.api.common import config_of, engine_of, envelope, lake_of
from tessera.impact import grant_thread, paper_thread, person_impact
from tessera.insight.inputs import source_stamps
from tessera.ledger.tables import person

router = APIRouter(prefix="/api/v1", tags=["impact"])

THREAD_SOURCES = ("reporter", "pubmed", "ctgov", "icite")


def _sources(request: Request) -> list[dict[str, Any]]:
    return source_stamps(engine_of(request), THREAD_SOURCES, config_of(request))


@router.get(
    "/grants/{core_project_num}/thread",
    summary="What followed a grant: papers, studies, and clinical articles, with each link",
)
def get_grant_thread(request: Request, core_project_num: str) -> dict[str, Any]:
    thread = grant_thread(engine_of(request), lake_of(request), core_project_num)
    if thread is None:
        raise HTTPException(status_code=404, detail="No such grant.")
    return envelope({**thread, "sources": _sources(request)})


@router.get(
    "/publications/{pmid}/thread",
    summary="The grants a paper acknowledges, and the studies and clinical articles after it",
)
def get_paper_thread(request: Request, pmid: str) -> dict[str, Any]:
    thread = paper_thread(engine_of(request), lake_of(request), pmid) if pmid.isdigit() else None
    if thread is None:
        raise HTTPException(status_code=404, detail="No such publication.")
    return envelope({**thread, "sources": _sources(request)})


@router.get(
    "/people/{person_id}/impact",
    summary="What followed a person's grants and their confirmed papers",
)
def get_person_impact(request: Request, person_id: str) -> dict[str, Any]:
    engine = engine_of(request)
    with engine.connect() as conn:
        found = conn.execute(select(person.c.id).where(person.c.id == person_id)).first()
    if found is None:
        raise HTTPException(status_code=404, detail="No such person.")
    impact = person_impact(engine, lake_of(request), person_id)
    return envelope({"person_id": person_id, **impact, "sources": _sources(request)})
