"""The expert finder, similar people, and what drives a profile (DIS-05, DIS-06, DIS-04).

"Who works on X?" becomes a small concept profile and people are ranked by how well theirs
matches it, each with the concepts that match. A query in words is read through the papers it
finds: MeSH has no synonyms in this build (the descriptor data is a candidate source), so
"cancer" would never meet "Neoplasms" by name, but papers about cancer carry that descriptor.
Concepts chosen directly (``msh:D009369``) are used as given. The concepts a query became are
returned, so the reader can see, and refine, how it was understood.
"""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path
from typing import Any

import pyarrow as pa

from tessera.evidence.authorship import CHECK_TAGS
from tessera.lake import Lake
from tessera.search.query import SearchIndex

QUERY_PAPERS = 200  # papers read to understand a query
QUERY_CONCEPTS = 12  # concepts kept in a query profile
MATCHES_SHOWN = 5


def _exists(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _people_with(cur: Any, profile: Path) -> dict[str, tuple[str, int]]:
    rows = cur.execute(
        "SELECT descriptor_ui, min(descriptor), count(*) FROM read_parquet(?) GROUP BY 1",
        [str(profile)],
    ).fetchall()
    return {ui: (name, n) for ui, name, n in rows}


def query_profile(
    lake: Lake, index: SearchIndex, profile: Path, q: str | None, concepts: list[str]
) -> list[dict[str, Any]]:
    """The concepts a query stands for, with weights (unit length)."""
    cur = lake.cursor()
    try:
        known = _people_with(cur, profile)
        population = cur.execute(
            "SELECT count(DISTINCT person_id) FROM read_parquet(?)", [str(profile)]
        ).fetchall()[0][0]

        def idf(ui: str) -> float:
            return math.log((population + 1) / known[ui][1])

        weights: Counter = Counter()
        for ui in concepts:
            ui = ui.removeprefix("msh:")
            if ui in known:
                weights[ui] += 3.0 * idf(ui)
        if q and q.strip():
            text = q.strip().lower()
            for ui, (name, _) in known.items():  # a concept named exactly: take it as meant
                if name.lower() == text:
                    weights[ui] += 3.0 * idf(ui)
            hits = index.search("publication", q, None, None, QUERY_PAPERS, 0, with_facets=False)
            ranked = [item["ref"].removeprefix("pub:") for item in hits.items]
            if ranked:
                rank = {pmid: i for i, pmid in enumerate(ranked)}
                cur.register("_hits", pa.table({"pmid": pa.array(ranked, pa.string())}))
                for pmid, ui, descriptor in cur.execute(
                    "SELECT pmid, descriptor_ui, descriptor "
                    "FROM pubmed_mesh JOIN _hits USING (pmid)"
                ).fetchall():
                    if ui in known and descriptor not in CHECK_TAGS:
                        weights[ui] += idf(ui) / (1 + rank[pmid] / 50)  # earlier hits count more
        top = weights.most_common(QUERY_CONCEPTS)
    finally:
        cur.close()
    norm = math.sqrt(sum(w * w for _, w in top)) or 1.0
    return [
        {"descriptor_ui": ui, "descriptor": known[ui][0], "weight": round(w / norm, 4)}
        for ui, w in top
        if w > 0
    ]


def find_experts(
    lake: Lake,
    index: SearchIndex,
    profile: Path,
    q: str | None,
    concepts: list[str],
    limit: int = 20,
) -> dict[str, Any]:
    if not _exists(profile):
        return {"concepts": [], "people": [], "ready": False}
    wanted = query_profile(lake, index, profile, q, concepts)
    if not wanted:
        return {"concepts": [], "people": [], "ready": True}
    cur = lake.cursor()
    try:
        cur.register(
            "_query",
            pa.table(
                {
                    "descriptor_ui": [c["descriptor_ui"] for c in wanted],
                    "q": [c["weight"] for c in wanted],
                }
            ),
        )
        rows = cur.execute(
            """
            SELECT person_id, sum(p.weight * q.q) AS score,
                   list_slice(list({'descriptor_ui': p.descriptor_ui,
                                    'descriptor': p.descriptor,
                                    'contribution': p.weight * q.q,
                                    'papers': p.papers}
                                   ORDER BY p.weight * q.q DESC), 1, ?) AS matches
            FROM read_parquet(?) p JOIN _query q USING (descriptor_ui)
            GROUP BY person_id ORDER BY score DESC, person_id LIMIT ?
            """,
            [MATCHES_SHOWN, str(profile), limit],
        ).fetchall()
    finally:
        cur.close()
    return {
        "concepts": wanted,
        "people": [
            {"id": pid, "score": round(score, 4), "matches": matches}
            for pid, score, matches in rows
            if score > 0
        ],
        "ready": True,
    }


def similar_people(lake: Lake, path: Path, person_id: str) -> list[dict[str, Any]]:
    if not _exists(path):
        return []
    cur = lake.cursor()
    try:
        rows = cur.execute(
            "SELECT other_id, score, shared FROM read_parquet(?) WHERE person_id = ? "
            "ORDER BY score DESC",
            [str(path), person_id],
        ).fetchall()
    finally:
        cur.close()
    return [{"id": o, "score": round(s, 4), "shared": shared} for o, s, shared in rows]


def person_concepts(
    lake: Lake, path: Path, person_id: str, limit: int = 30
) -> list[dict[str, Any]]:
    """What drives a profile: its top concepts, how many papers carry each, how many of
    those are confirmed, and whether a concept stands in for missing MeSH."""
    if not _exists(path):
        return []
    cur = lake.cursor()
    try:
        rows = cur.execute(
            "SELECT descriptor_ui, descriptor, weight, papers, confirmed, provisional "
            "FROM read_parquet(?) WHERE person_id = ? ORDER BY weight DESC LIMIT ?",
            [str(path), person_id, limit],
        ).fetchall()
    finally:
        cur.close()
    return [
        {
            "descriptor_ui": ui,
            "descriptor": name,
            "weight": round(w, 4),
            "papers": papers,
            "confirmed": confirmed,
            "provisional": provisional,
        }
        for ui, name, w, papers, confirmed, provisional in rows
    ]


def concept_names(lake: Lake, path: Path, text: str, limit: int = 10) -> list[dict[str, Any]]:
    """Concepts whose name contains the text, most widely held first (for choosing directly)."""
    if not _exists(path) or not text.strip():
        return []
    cur = lake.cursor()
    try:
        rows = cur.execute(
            "SELECT descriptor_ui, min(descriptor) AS name, count(*) AS people "
            "FROM read_parquet(?) WHERE contains(lower(descriptor), lower(?)) "
            "GROUP BY 1 ORDER BY people DESC, name LIMIT ?",
            [str(path), text.strip(), limit],
        ).fetchall()
    finally:
        cur.close()
    return [{"descriptor_ui": ui, "descriptor": name, "people": n} for ui, name, n in rows]


__all__ = ["concept_names", "find_experts", "person_concepts", "query_profile", "similar_people"]
