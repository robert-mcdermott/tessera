"""Graph views (DIS-07), missed connections (DIS-08), and the unit matrix (DIS-09).

The graph views return ``{"nodes": [...], "edges": [...]}`` with what a list view needs as
well, so the same response draws the graph and fills its accessible list.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from tessera.analytics.connections import groups, missed_connections, unit_matrix, unit_pairs
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


def _directory(request: Request) -> tuple[dict[str, dict[str, Any]], dict[str, set[str]]]:
    """Every directory member's units and status, and each unit's members (from the index)."""
    index = index_of(request)
    members = index.facet_refs("person", "unit")
    people: dict[str, dict[str, Any]] = {}
    for status, ids in index.facet_refs("person", "status").items():
        for pid in ids:
            people[pid] = {"status": status, "units": []}
    for unit, ids in members.items():
        for pid in ids:
            if pid in people:
                people[pid]["units"].append(unit)
    return people, members


def _person(named: dict[str, dict[str, Any]], pid: str) -> dict[str, Any]:
    p = named[pid]
    return {"id": pid, "name": p["name"], "units": p["units"]}


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
    named = _named(request, result["nodes"])
    grouped = groups(lake_of(request), request.app.state.home) if result["ready"] else {}
    for n in result["nodes"]:
        g = grouped.get(n["id"])
        n["cluster"] = g["group_label"] if g else None
        n["bridge"] = bool(g and g["bridge"])
    clusters: dict[str, int] = {}
    for n in result["nodes"]:
        if n["cluster"]:
            clusters[n["cluster"]] = clusters.get(n["cluster"], 0) + 1
    bridges = sorted(
        (
            {
                **_person(named, n["id"]),
                "groups": [
                    {"label": link["label"], "people": link["people"]}
                    for link in grouped[n["id"]]["links"]
                ],
            }
            for n in result["nodes"]
            if n["bridge"]
        ),
        key=lambda b: b["name"],
    )
    return envelope(
        {"nodes": result["nodes"], "edges": result["edges"]},
        ready=result["ready"],
        people=result["people"],
        shown=result["shown"],
        units=_units(request),
        groups=[
            {"label": k, "people": v}
            for k, v in sorted(clusters.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        bridges=bridges,
        note=None if result["ready"] else NOT_BUILT,
    )


@router.get("/missed", summary="Missed connections: alike, but never worked together")
def missed(
    request: Request,
    min_similarity: float = Query(0.25, ge=0.0, le=1.0),
    unit: str | None = Query(None, max_length=300, description="Either person in this unit"),
    across: bool = Query(False, description="Only pairs in different units"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    home = request.app.state.home
    people, _ = _directory(request)
    result = missed_connections(
        lake_of(request), home, people, min_similarity, unit, across, limit, offset
    )
    ids = {p for item in result["items"] for p in (item["a"], item["b"], *item["introducers"])}
    named = _people(request, sorted(ids))
    items = [
        {
            **item,
            "a": _person(named, item["a"]),
            "b": _person(named, item["b"]),
            "introducers": [_person(named, p) for p in item["introducers"]],
        }
        for item in result["items"]
    ]
    return envelope(
        items,
        total=result["total"],
        limit=limit,
        offset=offset,
        units=_units(request),
        ready=result["ready"],
        note=None if result["ready"] else NOT_BUILT,
    )


@router.get(
    "/units", summary="Unit by unit: how alike their interests are, and how much they work together"
)
def units_matrix(
    request: Request,
    max_units: int = Query(30, ge=2, le=60),
    min_similarity: float = Query(0.25, ge=0.0, le=1.0),
) -> dict[str, Any]:
    _, members = _directory(request)
    result = unit_matrix(
        lake_of(request), request.app.state.home, members, max_units, min_similarity
    )
    return envelope(
        {"units": result["units"], "cells": result["cells"]},
        ready=result["ready"],
        available=result["available"],
        note=None if result["ready"] else NOT_BUILT,
    )


@router.get("/units/pairs", summary="The pairs of people behind one cell of the unit matrix")
def units_pairs(
    request: Request,
    a: str = Query(..., max_length=300),
    b: str = Query(..., max_length=300),
) -> dict[str, Any]:
    _, members = _directory(request)
    if a not in members or b not in members:
        raise HTTPException(status_code=404, detail="No such unit.")
    pairs = unit_pairs(lake_of(request), request.app.state.home, members[a], members[b])
    named = _people(request, sorted({p for x in pairs for p in (x["a"], x["b"])}))
    return envelope(
        [{**x, "a": _person(named, x["a"]), "b": _person(named, x["b"])} for x in pairs],
        total=len(pairs),
        units=[a, b],
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
