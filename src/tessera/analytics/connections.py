"""Missed connections and the unit matrix (DIS-08, DIS-09).

Both read precomputed files (similar pairs, collaboration pairs, groups) and filter them;
nothing is recomputed when someone looks.

* **Missed connections** are pairs of current members whose interests are alike (similarity at
  or above a threshold) with no shared paper, grant, or study. Each comes with the concepts
  they share and, where there is one, a collaborator of both who could introduce them.
* **The unit matrix** compares units pairwise: how alike their members' interests are, and
  how much they actually work together, each as a density (per possible pair of members), so
  large and small units compare fairly. Every cell opens the pairs behind it.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from tessera.analytics.concepts import similarity_path
from tessera.analytics.graph import _collab_edge, _ready, _rows, collaboration_path, group_path
from tessera.home import DataHome
from tessera.lake import Lake

INTRODUCERS = 3


def _similar_pairs(lake: Lake, home: DataHome, floor: float = 0.0) -> list[dict[str, Any]]:
    return _rows(
        lake,
        "SELECT person_id AS a, other_id AS b, score, shared FROM read_parquet(?) "
        "WHERE person_id < other_id AND score >= ? ORDER BY score DESC, a, b",
        [str(similarity_path(home)), floor],
    )


def _collaboration(lake: Lake, home: DataHome) -> dict[tuple[str, str], dict[str, Any]]:
    rows = _rows(lake, "SELECT * FROM read_parquet(?)", [str(collaboration_path(home))])
    return {(r["person_id"], r["other_id"]): _collab_edge(r) for r in rows}


def collaborators_of(lake: Lake, home: DataHome, person_id: str) -> dict[str, dict[str, Any]]:
    """The people someone has worked with, keyed by their id."""
    if not collaboration_path(home).is_file():
        return {}
    rows = _rows(
        lake,
        "SELECT * FROM read_parquet(?) WHERE person_id = ? OR other_id = ?",
        [str(collaboration_path(home)), person_id, person_id],
    )
    return {
        (r["other_id"] if r["person_id"] == person_id else r["person_id"]): _collab_edge(r)
        for r in rows
    }


def _shared(items: list[dict[str, Any]], n: int = 5) -> list[dict[str, Any]]:
    return [
        {
            "descriptor_ui": s["descriptor_ui"],
            "descriptor": s["descriptor"],
            "contribution": round(s["contribution"], 4),
        }
        for s in items[:n]
    ]


def missed_connections(
    lake: Lake,
    home: DataHome,
    people: dict[str, dict[str, Any]],
    min_similarity: float = 0.25,
    unit: str | None = None,
    across: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Alike, current, and never worked together; most alike first.

    ``people`` maps person id to its summary (``units``, ``status``); only people in it
    appear, and former members never do.
    """
    if not _ready(home):
        return {"ready": False, "items": [], "total": 0}
    together = _collaboration(lake, home)
    neighbours: dict[str, dict[str, float]] = defaultdict(dict)
    for (a, b), e in together.items():
        neighbours[a][b] = e["weight"]
        neighbours[b][a] = e["weight"]
    found = []
    for r in _similar_pairs(lake, home, min_similarity):
        a, b = r["a"], r["b"]
        if (a, b) in together or a not in people or b not in people:
            continue
        pa_, pb = people[a], people[b]
        if pa_.get("status") == "former" or pb.get("status") == "former":
            continue
        ua, ub = set(pa_.get("units") or []), set(pb.get("units") or [])
        if unit and unit not in ua | ub:
            continue
        if across and (not ua or not ub or ua & ub):
            continue
        found.append(r)
    items = []
    for r in found[offset : offset + limit]:
        a, b = r["a"], r["b"]
        common = set(neighbours[a]) & set(neighbours[b])
        introducers = sorted(common, key=lambda c: (-min(neighbours[a][c], neighbours[b][c]), c))[
            :INTRODUCERS
        ]
        items.append(
            {
                "a": a,
                "b": b,
                "score": round(r["score"], 4),
                "shared": _shared(r["shared"]),
                "introducers": introducers,
            }
        )
    return {"ready": True, "items": items, "total": len(found)}


def _pairs_between(size_a: int, size_b: int, overlap: int, same: bool) -> int:
    """Distinct pairs of two different people, one from each unit."""
    if same:
        return size_a * (size_a - 1) // 2
    return size_a * size_b - overlap - overlap * (overlap - 1) // 2


def unit_matrix(
    lake: Lake,
    home: DataHome,
    members: dict[str, set[str]],
    max_units: int = 30,
    min_similarity: float = 0.25,
) -> dict[str, Any]:
    """Unit-by-unit interest and collaboration, as densities, units ordered by interest."""
    if not _ready(home):
        return {"ready": False, "units": [], "cells": [], "available": 0}
    sized = sorted(
        ((u, ids) for u, ids in members.items() if len(ids) >= 2),
        key=lambda kv: (-len(kv[1]), kv[0]),
    )
    chosen = dict(sized[:max_units])
    units_of: dict[str, list[str]] = defaultdict(list)
    for u, ids in chosen.items():
        for p in ids:
            units_of[p].append(u)

    def keys(a: str, b: str) -> set[tuple[str, str]]:
        return {(min(x, y), max(x, y)) for x in units_of.get(a, []) for y in units_of.get(b, [])}

    cells: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    together = _collaboration(lake, home)
    for r in _similar_pairs(lake, home):
        for k in keys(r["a"], r["b"]):
            cells[k]["similarity"] += r["score"]
            cells[k]["similar_pairs"] += 1
            if r["score"] >= min_similarity and (r["a"], r["b"]) not in together:
                cells[k]["missed"] += 1
    for a, b in together:
        for k in keys(a, b):
            cells[k]["collaborating_pairs"] += 1
    names = sorted(chosen)
    out = []
    for i, x in enumerate(names):
        for y in names[i:]:
            possible = _pairs_between(
                len(chosen[x]), len(chosen[y]), len(chosen[x] & chosen[y]), x == y
            )
            c = cells.get((x, y), {})
            out.append(
                {
                    "a": x,
                    "b": y,
                    "possible": possible,
                    "similar_pairs": int(c.get("similar_pairs", 0)),
                    "similarity": round(c.get("similarity", 0.0) / possible, 4)
                    if possible
                    else 0.0,
                    "collaborating_pairs": int(c.get("collaborating_pairs", 0)),
                    "collaboration": round(c.get("collaborating_pairs", 0) / possible, 4)
                    if possible
                    else 0.0,
                    "missed": int(c.get("missed", 0)),
                }
            )
    # Units ordered by how alike their members are to everyone else's ("sorted by density").
    density: dict[str, float] = defaultdict(float)
    for c in out:
        density[c["a"]] += c["similarity"]
        if c["a"] != c["b"]:
            density[c["b"]] += c["similarity"]
    order = sorted(names, key=lambda u: (-density[u], u))
    return {
        "ready": True,
        "units": [{"name": u, "members": len(chosen[u])} for u in order],
        "cells": out,
        "available": len(sized),
    }


def unit_pairs(
    lake: Lake, home: DataHome, first: set[str], second: set[str]
) -> list[dict[str, Any]]:
    """The pairs behind one cell: alike or working together, one person from each unit."""
    together = _collaboration(lake, home)
    alike = {(r["a"], r["b"]): r for r in _similar_pairs(lake, home)}
    out = []
    for key in sorted(set(alike) | set(together)):
        a, b = key
        if not ((a in first and b in second) or (a in second and b in first)):
            continue
        if a in first and b in second:
            left, right = a, b
        else:
            left, right = b, a
        sim = alike.get(key)
        out.append(
            {
                "a": left,
                "b": right,
                "score": round(sim["score"], 4) if sim else None,
                "shared": _shared(sim["shared"]) if sim else [],
                "together": together.get(key),
            }
        )
    out.sort(key=lambda p: (-(p["score"] or 0), -((p["together"] or {}).get("weight") or 0)))
    return out


def groups(lake: Lake, home: DataHome) -> dict[str, dict[str, Any]]:
    """Each collaborating person's group, betweenness, and whether they bridge groups."""
    if not group_path(home).is_file():
        return {}
    return {
        r["person_id"]: r
        for r in _rows(lake, "SELECT * FROM read_parquet(?)", [str(group_path(home))])
    }


__all__ = [
    "collaborators_of",
    "groups",
    "missed_connections",
    "unit_matrix",
    "unit_pairs",
]
