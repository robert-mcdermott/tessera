"""Graph views: person focus, collaboration network, and concept landscape (DIS-07).

Each returns ``{"nodes": [...], "edges": [...]}`` with what a list view needs as well, so the
same response draws the graph and fills its accessible list.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter, Query, Request

from tessera.analytics.graph import landscape, network, person_focus
from tessera.api.common import envelope, index_of, lake_of
from tessera.api.routers.discovery import NOT_BUILT, _people
from tessera.api.routers.people import _person_row

router = APIRouter(prefix="/api/v1/graph", tags=["discovery"])


def _named(
    request: Request, nodes: list[dict[str, Any]], extra: Iterable[str] = ()
) -> dict[str, dict[str, Any]]:
    people = _people(request, [n["id"] for n in nodes if n["kind"] == "person"] + list(extra))
    for n in nodes:
        if n["kind"] == "person":
            p = people[n["id"]]
            n.update(label=p["name"], units=p["units"], status=p["status"])
            n["group"] = p["units"][0] if p["units"] else None
    return people


def _units(request: Request) -> list[dict[str, Any]]:
    try:
        return index_of(request).facet_values("person", "unit")
    except Exception:  # the index is optional here, as for names
        return []


@router.get("/focus/{person_id}", summary="A person, their top concepts, and who shares them")
def focus(
    request: Request,
    person_id: str,
    concepts: int = Query(10, ge=3, le=20, description="The person's top concepts shown"),
    people: int = Query(30, ge=5, le=100, description="People sharing them, at most"),
) -> dict[str, Any]:
    _person_row(request, person_id)
    result = person_focus(lake_of(request), request.app.state.home, person_id, concepts, people)
    named = _named(request, result["nodes"], [c["target"] for c in result["collaborators"]])
    collaborators = [
        {**c, "name": named[c["target"]]["name"], "units": named[c["target"]]["units"]}
        for c in result["collaborators"]
    ]
    return envelope(
        {"nodes": result["nodes"], "edges": result["edges"]},
        ready=result["ready"],
        collaborators=collaborators,
        note=None if result["ready"] else NOT_BUILT,
    )


@router.get("/network", summary="People linked by collaboration and, without it, by similarity")
def collaboration_network(
    request: Request,
    unit: str | None = Query(None, max_length=300, description="Only people in this unit"),
    max_people: int = Query(150, ge=10, le=400),
    min_similarity: float = Query(0.25, ge=0.0, le=1.0),
    similar: bool = Query(True, description="Show similar people who have not worked together"),
) -> dict[str, Any]:
    allowed = index_of(request).refs_with("person", "unit", unit) if unit else None
    result = network(
        lake_of(request),
        request.app.state.home,
        allowed,
        max_people=max_people,
        min_similarity=min_similarity,
        similar=similar,
    )
    _named(request, result["nodes"])
    return envelope(
        {"nodes": result["nodes"], "edges": result["edges"]},
        ready=result["ready"],
        people=result["people"],
        shown=result["shown"],
        units=_units(request),
        note=None if result["ready"] else NOT_BUILT,
    )


@router.get("/landscape", summary="Concepts linked by the people who hold them together")
def concept_landscape(
    request: Request,
    max_concepts: int = Query(150, ge=10, le=400),
    min_people: int = Query(2, ge=1, le=50),
) -> dict[str, Any]:
    result = landscape(
        lake_of(request), request.app.state.home, max_concepts=max_concepts, min_people=min_people
    )
    return envelope(
        {"nodes": result["nodes"], "edges": result["edges"]},
        ready=result["ready"],
        concepts=result["concepts"],
        shown=len(result["nodes"]),
        note=None if result["ready"] else NOT_BUILT,
    )
