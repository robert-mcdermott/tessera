"""Discovery: the expert finder, similar people, and concept profiles (DIS-04 to DIS-06)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from tessera.analytics.concepts import profile_path, similarity_path
from tessera.analytics.connections import collaborators_of
from tessera.analytics.experts import (
    concept_names,
    find_experts,
    person_concepts,
    similar_people,
)
from tessera.api.common import envelope, index_of, lake_of, split_values
from tessera.api.routers.people import _person_row

router = APIRouter(prefix="/api/v1", tags=["discovery"])

NOT_BUILT = (
    "Concept profiles have not been built yet; they are built by sync or `tessera build discovery`."
)


def _people(request: Request, ids: list[str]) -> dict[str, dict[str, Any]]:
    try:
        summaries = index_of(request).summaries(ids)
    except Exception:  # the index is optional here
        summaries = {}
    return {
        pid: {
            "id": pid,
            "name": (summaries.get(pid) or {}).get("name", pid),
            "units": (summaries.get(pid) or {}).get("units", []),
            "status": (summaries.get(pid) or {}).get("status"),
        }
        for pid in ids
    }


@router.get("/experts", summary="Who works on something: people ranked by matching concepts")
def experts(
    request: Request,
    q: str | None = Query(
        None, max_length=300, description="Words, read through the papers they find"
    ),
    concept: Annotated[
        list[str] | None, Query(description="MeSH descriptor IDs (D009369 or msh:D009369)")
    ] = None,
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    home = request.app.state.home
    result = find_experts(
        lake_of(request), index_of(request), profile_path(home), q, split_values(concept), limit
    )
    people = _people(request, [p["id"] for p in result["people"]])
    items = [
        {**people[p["id"]], "score": p["score"], "matches": p["matches"]} for p in result["people"]
    ]
    return envelope(
        items,
        total=len(items),
        concepts=result["concepts"],
        ready=result["ready"],
        note=None if result["ready"] else NOT_BUILT,
    )


@router.get("/concepts", summary="Concepts by name, most widely held first")
def concepts(request: Request, q: str = Query("", max_length=200)) -> dict[str, Any]:
    home = request.app.state.home
    return envelope(concept_names(lake_of(request), profile_path(home), q))


@router.get(
    "/people/{person_id}/similar", summary="People whose concept profiles are most like this one"
)
def similar(request: Request, person_id: str) -> dict[str, Any]:
    _person_row(request, person_id)
    home = request.app.state.home
    rows = similar_people(lake_of(request), similarity_path(home), person_id)
    people = _people(request, [r["id"] for r in rows])
    worked = collaborators_of(lake_of(request), home, person_id)
    return envelope(
        [
            {
                **people[r["id"]],
                "score": r["score"],
                "shared": r["shared"],
                "together": worked.get(r["id"]),
            }
            for r in rows
        ]
    )


@router.get("/people/{person_id}/concepts", summary="What drives this person's concept profile")
def concepts_of(request: Request, person_id: str) -> dict[str, Any]:
    _person_row(request, person_id)
    home = request.app.state.home
    return envelope(
        person_concepts(lake_of(request), profile_path(home), person_id),
        built=profile_path(home).is_file(),
    )
