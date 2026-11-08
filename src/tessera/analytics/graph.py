"""The collaboration graph and the concept landscape (DIS-07).

**Collaboration** between two directory members is what they share: papers both are linked
to (counted as for concept profiles: confirmed, or a strong suggestion while
``discovery.likely_weight`` is above 0), grants both are investigators on, and studies both
are officials of. Each shared record adds ``1 / (k - 1)`` to the pair's strength, where k is
the number of members on it, so one consortium paper with forty members does not outweigh a
long partnership of two. Pairs are written to ``derived/collaboration_edge.parquet``.

**The concept landscape** links two concepts when people hold both among their top
concepts; ``derived/concept_edge.parquet`` keeps pairs that at least two people share.

The views read these files and the concept profiles, filter them, and return nodes and
edges; nothing is recomputed when someone looks. Every view is bounded (about 150 nodes by
default) so it lays out quickly and stays legible.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import pyarrow as pa
from sqlalchemy.engine import Engine

from tessera.analytics.concepts import (
    _write,
    linked_records,
    profile_path,
    similarity_path,
)
from tessera.analytics.groups import build_groups
from tessera.config import Config
from tessera.home import DataHome
from tessera.lake import Lake

log = logging.getLogger("tessera.analytics")

COLLABORATION_FILE = "collaboration_edge.parquet"
CONCEPT_EDGE_FILE = "concept_edge.parquet"
GROUP_FILE = "person_group.parquet"
LANDSCAPE_TOP = 15  # each person's top concepts that make up the landscape
RECENT_SHOWN = 5


def collaboration_path(home: DataHome) -> Path:
    return home.derived / COLLABORATION_FILE


def concept_edge_path(home: DataHome) -> Path:
    return home.derived / CONCEPT_EDGE_FILE


def group_path(home: DataHome) -> Path:
    return home.derived / GROUP_FILE


COLLABORATION_SQL = """
WITH links AS (
  SELECT DISTINCT person_id, 'paper' AS kind, pmid AS ref, confirmed FROM _papers
  UNION ALL SELECT DISTINCT person_id, 'grant', core, true FROM _grants
  UNION ALL SELECT DISTINCT person_id, 'study', nct_id, factor = 1.0 FROM _studies
),
sizes AS (
  SELECT kind, ref, count(DISTINCT person_id) AS k FROM links GROUP BY kind, ref HAVING k > 1
),
shared AS (
  SELECT a.person_id AS a, b.person_id AS b, a.kind, a.ref,
         a.confirmed AND b.confirmed AS confirmed, 1.0 / (s.k - 1) AS part
  FROM links a
  JOIN links b ON a.kind = b.kind AND a.ref = b.ref AND a.person_id < b.person_id
  JOIN sizes s ON s.kind = a.kind AND s.ref = a.ref
),
years AS (SELECT pmid, max(pub_year) AS year FROM pubmed_article GROUP BY pmid),
dated AS (
  SELECT shared.*, y.year FROM shared
  LEFT JOIN years y ON shared.kind = 'paper' AND y.pmid = shared.ref
)
SELECT a AS person_id, b AS other_id,
       count(*) FILTER (WHERE kind = 'paper') AS papers,
       count(*) FILTER (WHERE kind = 'paper' AND confirmed) AS confirmed_papers,
       count(*) FILTER (WHERE kind = 'grant') AS grants,
       count(*) FILTER (WHERE kind = 'study') AS studies,
       sum(part) AS strength,
       min(year) AS first_year, max(year) AS last_year,
       coalesce(list_slice(list(ref ORDER BY year DESC NULLS LAST, ref)
                           FILTER (WHERE kind = 'paper'), 1, ?), []) AS recent
FROM dated GROUP BY a, b ORDER BY a, b
"""

CONCEPT_EDGE_SQL = """
WITH top AS (
  SELECT person_id, descriptor_ui AS ui, weight FROM read_parquet(?)
  QUALIFY row_number() OVER (PARTITION BY person_id ORDER BY weight DESC, descriptor_ui) <= ?
)
SELECT a.ui AS concept_id, b.ui AS other_id, count(*) AS people,
       sum(least(a.weight, b.weight)) AS strength
FROM top a JOIN top b ON a.person_id = b.person_id AND a.ui < b.ui
GROUP BY 1, 2 HAVING count(*) >= 2
ORDER BY 1, 2
"""


def build_graph(config: Config, home: DataHome, engine: Engine) -> dict[str, Any]:
    """Write the collaboration and concept edges (after the concept profiles)."""
    papers, studies, grants = linked_records(engine, config)
    with Lake(home.lake) as lake:
        cur = lake.cursor()
        try:
            cur.register("_papers", papers)
            cur.register("_studies", studies)
            cur.register("_grants", grants)
            collaboration = cur.sql(COLLABORATION_SQL, params=[RECENT_SHOWN]).to_arrow_table()
            if profile_path(home).is_file():
                concepts = cur.sql(
                    CONCEPT_EDGE_SQL, params=[str(profile_path(home)), LANDSCAPE_TOP]
                ).to_arrow_table()
            else:
                concepts = pa.table(
                    {
                        "concept_id": pa.array([], pa.string()),
                        "other_id": pa.array([], pa.string()),
                        "people": pa.array([], pa.int64()),
                        "strength": pa.array([], pa.float64()),
                    }
                )
        finally:
            cur.close()
    groups = build_groups(collaboration, profile_path(home))
    home.derived.mkdir(parents=True, exist_ok=True)
    _write(collaboration, collaboration_path(home))
    _write(concepts, concept_edge_path(home))
    _write(groups, group_path(home))
    group_count = len(set(groups.column("group_id").to_pylist()))
    bridges = sum(groups.column("bridge").to_pylist())
    log.info(
        "collaboration graph: %d pairs, %d groups, %d bridges; concept landscape: %d pairs",
        collaboration.num_rows,
        group_count,
        bridges,
        concepts.num_rows,
    )
    return {
        "collaborations": collaboration.num_rows,
        "groups": group_count,
        "bridges": bridges,
        "concept_pairs": concepts.num_rows,
    }


# --- views ------------------------------------------------------------------------------------


def _ready(home: DataHome) -> bool:
    return all(
        p.is_file() and p.stat().st_size > 0
        for p in (profile_path(home), similarity_path(home), collaboration_path(home))
    )


def _rows(lake: Lake, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    cur = lake.cursor()
    try:
        result = cur.execute(sql, params)
        names = [d[0] for d in result.description]
        return [dict(zip(names, row, strict=True)) for row in result.fetchall()]
    finally:
        cur.close()


def _collab_edge(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": r["person_id"],
        "target": r["other_id"],
        "kind": "collaboration",
        "weight": round(r["strength"], 4),
        "papers": r["papers"],
        "confirmed_papers": r["confirmed_papers"],
        "grants": r["grants"],
        "studies": r["studies"],
        "first_year": r["first_year"],
        "last_year": r["last_year"],
        "recent": list(r["recent"] or []),
    }


def person_focus(
    lake: Lake, home: DataHome, person_id: str, concepts: int = 10, people: int = 30
) -> dict[str, Any]:
    """One person, their top concepts, and the people who share them: person-concept edges
    only, so people gather around what they share instead of tangling around the focus."""
    if not _ready(home):
        return {"ready": False, "nodes": [], "edges": [], "collaborators": []}
    profile = str(profile_path(home))
    mine = _rows(
        lake,
        "SELECT descriptor_ui, descriptor, weight, papers FROM read_parquet(?) "
        "WHERE person_id = ? ORDER BY weight DESC, descriptor_ui LIMIT ?",
        [profile, person_id, concepts],
    )
    collaborators = [
        _collab_edge(r)
        for r in _rows(
            lake,
            "SELECT * FROM read_parquet(?) WHERE person_id = ? OR other_id = ? "
            "ORDER BY strength DESC, papers DESC LIMIT 10",
            [str(collaboration_path(home)), person_id, person_id],
        )
    ]
    for edge in collaborators:  # oriented away from the focus
        if edge["target"] == person_id:
            edge["source"], edge["target"] = edge["target"], edge["source"]
    nodes: list[dict[str, Any]] = [{"id": person_id, "kind": "person", "focus": True}]
    edges: list[dict[str, Any]] = []
    if not mine:
        return {"ready": True, "nodes": nodes, "edges": edges, "collaborators": collaborators}
    weight = {c["descriptor_ui"]: c["weight"] for c in mine}
    for c in mine:
        nodes.append(
            {
                "id": f"msh:{c['descriptor_ui']}",
                "kind": "concept",
                "label": c["descriptor"],
                "papers": c["papers"],
            }
        )
        edges.append(
            {
                "source": person_id,
                "target": f"msh:{c['descriptor_ui']}",
                "kind": "concept",
                "weight": round(c["weight"], 4),
            }
        )
    held = _rows(
        lake,
        "SELECT person_id, descriptor_ui, weight FROM read_parquet(?) "
        "WHERE person_id <> ? AND list_contains(?, descriptor_ui)",
        [profile, person_id, list(weight)],
    )
    overlap: dict[str, float] = defaultdict(float)
    for r in held:
        overlap[r["person_id"]] += weight[r["descriptor_ui"]] * r["weight"]
    chosen = sorted(overlap, key=lambda p: (-overlap[p], p))[:people]
    keep = set(chosen)
    for p in chosen:
        nodes.append({"id": p, "kind": "person", "overlap": round(overlap[p], 4)})
    for r in held:
        if r["person_id"] in keep:
            edges.append(
                {
                    "source": r["person_id"],
                    "target": f"msh:{r['descriptor_ui']}",
                    "kind": "concept",
                    "weight": round(r["weight"], 4),
                }
            )
    return {"ready": True, "nodes": nodes, "edges": edges, "collaborators": collaborators}


def network(
    lake: Lake,
    home: DataHome,
    allowed: set[str] | None = None,
    max_people: int = 150,
    min_similarity: float = 0.25,
    similar: bool = True,
) -> dict[str, Any]:
    """People, linked by collaboration (solid) and, where they have none, by similarity
    (dashed): "who works together, and who should?"."""
    if not _ready(home):
        return {"ready": False, "nodes": [], "edges": [], "people": 0, "shown": 0}
    collab = [
        _collab_edge(r)
        for r in _rows(lake, "SELECT * FROM read_parquet(?)", [str(collaboration_path(home))])
    ]
    together = {(e["source"], e["target"]) for e in collab}
    near = []
    if similar:
        for r in _rows(
            lake,
            "SELECT person_id, other_id, score, shared FROM read_parquet(?) "
            "WHERE person_id < other_id AND score >= ?",
            [str(similarity_path(home)), min_similarity],
        ):
            if (r["person_id"], r["other_id"]) not in together:
                near.append(
                    {
                        "source": r["person_id"],
                        "target": r["other_id"],
                        "kind": "similarity",
                        "weight": round(r["score"], 4),
                        "shared": [
                            {"descriptor_ui": s["descriptor_ui"], "descriptor": s["descriptor"]}
                            for s in r["shared"][:3]
                        ],
                    }
                )
    edges = [
        e
        for e in collab + near
        if allowed is None or (e["source"] in allowed and e["target"] in allowed)
    ]
    # Bounded: the people with the most collaboration (then similarity) are kept.
    degree: dict[str, float] = defaultdict(float)
    for e in edges:
        part = e["weight"] if e["kind"] == "collaboration" else e["weight"] / 4
        degree[e["source"]] += part
        degree[e["target"]] += part
    ranked = sorted(degree, key=lambda p: (-degree[p], p))
    keep = set(ranked[:max_people])
    edges = [e for e in edges if e["source"] in keep and e["target"] in keep]
    linked = {p for e in edges for p in (e["source"], e["target"])}
    nodes = [
        {"id": p, "kind": "person", "degree": round(degree[p], 4)} for p in ranked if p in linked
    ]
    return {
        "ready": True,
        "nodes": nodes,
        "edges": edges,
        "people": len(degree),
        "shown": len(nodes),
    }


def landscape(
    lake: Lake, home: DataHome, max_concepts: int = 150, min_people: int = 2, per_concept: int = 5
) -> dict[str, Any]:
    """Concepts, sized by how many people hold them among their top concepts, linked by
    co-occurrence: "what are our research areas, and how do they connect?"."""
    if not (_ready(home) and concept_edge_path(home).is_file()):
        return {"ready": False, "nodes": [], "edges": [], "concepts": 0}
    held = _rows(
        lake,
        """
        WITH top AS (
          SELECT person_id, descriptor_ui, descriptor, weight FROM read_parquet(?)
          QUALIFY row_number() OVER (PARTITION BY person_id ORDER BY weight DESC, descriptor_ui)
                  <= ?
        )
        SELECT descriptor_ui, min(descriptor) AS descriptor, count(*) AS people,
               sum(weight) AS mass
        FROM top GROUP BY 1 HAVING count(*) >= ? ORDER BY people DESC, mass DESC, 1
        """,
        [str(profile_path(home)), LANDSCAPE_TOP, min_people],
    )
    total = len(held)
    held = held[:max_concepts]
    keep = {c["descriptor_ui"] for c in held}
    pairs = [
        r
        for r in _rows(
            lake,
            "SELECT * FROM read_parquet(?) ORDER BY strength DESC, concept_id, other_id",
            [str(concept_edge_path(home))],
        )
        if r["concept_id"] in keep and r["other_id"] in keep
    ]
    # Each concept keeps its strongest links, so the landscape shows structure, not a hairball.
    count: dict[str, int] = defaultdict(int)
    chosen = []
    for r in pairs:
        a, b = r["concept_id"], r["other_id"]
        if count[a] < per_concept or count[b] < per_concept:
            count[a] += 1
            count[b] += 1
            chosen.append(
                {
                    "source": f"msh:{a}",
                    "target": f"msh:{b}",
                    "kind": "cooccurrence",
                    "weight": round(r["strength"], 4),
                    "people": r["people"],
                }
            )
    linked = {e[end] for e in chosen for end in ("source", "target")}
    nodes = [
        {
            "id": f"msh:{c['descriptor_ui']}",
            "kind": "concept",
            "label": c["descriptor"],
            "people": c["people"],
            "size": round(math.sqrt(c["people"]), 3),
        }
        for c in held
        if f"msh:{c['descriptor_ui']}" in linked  # a topic no one holds with another is noise here
    ]
    return {"ready": True, "nodes": nodes, "edges": chosen, "concepts": total}


__all__ = [
    "build_graph",
    "collaboration_path",
    "concept_edge_path",
    "landscape",
    "network",
    "person_focus",
]
