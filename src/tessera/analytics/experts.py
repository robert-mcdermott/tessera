"""The expert finder, similar people, and what drives a profile (DIS-05, DIS-06, DIS-04).

"Who works on X?" becomes a small concept profile and people are ranked by how well theirs
matches it, each with the concepts that match. A query is understood three ways:

* **Headings and synonyms.** Words that name a MeSH heading, or one of its entry terms, are
  taken as that heading: "cancer" is "Neoplasms" (with NLM's MeSH data; see vocabulary.py).
* **Through papers.** The organization's papers that the words find carry headings too, so a
  query reaches concepts it never names.
* **Chosen directly.** Concepts picked by identifier (``msh:D009369``) are used as given.

A broad heading includes its narrower ones: asking for "Neoplasms" also finds people whose
papers are indexed under "Breast Neoplasms", at a lower weight. The concepts a query became
are returned, so the reader can see, and refine, how it was understood.
"""

from __future__ import annotations

import math
import re
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
NARROWER = 0.7  # how much a narrower heading counts toward a broad one asked for
LONGEST_PHRASE = 6  # words


def _exists(path: Path | None) -> bool:
    return path is not None and path.is_file() and path.stat().st_size > 0


def _people_with(cur: Any, profile: Path) -> dict[str, tuple[str, int]]:
    rows = cur.execute(
        "SELECT descriptor_ui, min(descriptor), count(*) FROM read_parquet(?) GROUP BY 1",
        [str(profile)],
    ).fetchall()
    return {ui: (name, n) for ui, name, n in rows}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _vocabulary(cur: Any, vocabulary: Path | None) -> list[dict[str, Any]]:
    if not _exists(vocabulary):
        return []
    result = cur.execute(
        "SELECT descriptor_ui, name, tree_numbers, entry_terms, people, people_below "
        "FROM read_parquet(?)",
        [str(vocabulary)],
    )
    names = [d[0] for d in result.description]
    return [dict(zip(names, row, strict=True)) for row in result.fetchall()]


def query_profile(
    lake: Lake,
    index: SearchIndex,
    profile: Path,
    q: str | None,
    concepts: list[str],
    vocabulary: Path | None = None,
) -> list[dict[str, Any]]:
    """The concepts a query stands for, with weights (unit length)."""
    cur = lake.cursor()
    try:
        known = _people_with(cur, profile)
        population = cur.execute(
            "SELECT count(DISTINCT person_id) FROM read_parquet(?)", [str(profile)]
        ).fetchall()[0][0]
        vocab = {v["descriptor_ui"]: v for v in _vocabulary(cur, vocabulary) if v["people_below"]}
        # A heading can be asked for if someone holds it or anything beneath it.
        holders = {ui: n for ui, (_, n) in known.items()}
        for ui, v in vocab.items():
            holders[ui] = max(holders.get(ui, 0), v["people_below"])
        name_of = {ui: name for ui, (name, _) in known.items()} | {
            ui: v["name"] for ui, v in vocab.items()
        }

        def idf(ui: str) -> float:
            return math.log((population + 1) / holders[ui])

        weights: Counter = Counter()
        via: dict[str, str] = {}
        named: set[str] = set()  # concepts the query names outright, or that were chosen
        for ui in concepts:
            ui = ui.removeprefix("msh:")
            if ui in holders:
                weights[ui] += 3.0 * idf(ui)
                named.add(ui)
        if q and q.strip():
            # Phrases that name a heading or one of its synonyms, longest first.
            terms: dict[str, str] = {}
            for ui, name in name_of.items():
                terms.setdefault(" ".join(_words(name)), ui)
            for ui, v in vocab.items():
                for term in v["entry_terms"] or []:
                    terms.setdefault(" ".join(_words(term)), ui)
            words = _words(q)
            used = [False] * len(words)
            for size in range(min(LONGEST_PHRASE, len(words)), 0, -1):
                for i in range(len(words) - size + 1):
                    if any(used[i : i + size]):
                        continue
                    phrase = " ".join(words[i : i + size])
                    ui = terms.get(phrase)
                    if ui and ui in holders and name_of[ui] not in CHECK_TAGS:
                        weights[ui] += 3.0 * idf(ui)
                        named.add(ui)
                        if phrase != " ".join(_words(name_of[ui])):
                            via[ui] = phrase
                        used[i : i + size] = [True] * size
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
        # What the query names leads what its papers suggest, however broad the heading.
        others = max((w for ui, w in weights.items() if ui not in named), default=0.0)
        for ui in named:
            weights[ui] = max(weights[ui], 1.2 * others)
        top = weights.most_common(QUERY_CONCEPTS)
    finally:
        cur.close()
    norm = math.sqrt(sum(w * w for _, w in top)) or 1.0
    return [
        {
            "descriptor_ui": ui,
            "descriptor": name_of[ui],
            "weight": round(w / norm, 4),
            **({"via": via[ui]} if ui in via else {}),
        }
        for ui, w in top
        if w > 0
    ]


def _expanded(
    wanted: list[dict[str, Any]], vocab: list[dict[str, Any]], known: set[str]
) -> pa.Table:
    """Each query concept with the held headings that count toward it: itself, and the
    narrower headings beneath it (at ``NARROWER``)."""
    trees_of = {v["descriptor_ui"]: v["tree_numbers"] or [] for v in vocab}
    held_trees = [
        (v["descriptor_ui"], t)
        for v in vocab
        if v["descriptor_ui"] in known
        for t in v["tree_numbers"] or []
    ]
    rows: dict[str, list[Any]] = {"query_ui": [], "member_ui": [], "factor": [], "q": []}
    for c in wanted:
        ui = c["descriptor_ui"]
        members = {ui: 1.0} if ui in known else {}
        prefixes = [f"{t}." for t in trees_of.get(ui, [])]
        for member, tree in held_trees:
            if member not in members and any(tree.startswith(p) for p in prefixes):
                members[member] = NARROWER
        for member, factor in members.items():
            rows["query_ui"].append(ui)
            rows["member_ui"].append(member)
            rows["factor"].append(factor)
            rows["q"].append(c["weight"])
    return pa.table(rows)


def find_experts(
    lake: Lake,
    index: SearchIndex,
    profile: Path,
    q: str | None,
    concepts: list[str],
    limit: int = 20,
    vocabulary: Path | None = None,
) -> dict[str, Any]:
    if not _exists(profile):
        return {"concepts": [], "people": [], "ready": False}
    wanted = query_profile(lake, index, profile, q, concepts, vocabulary)
    if not wanted:
        return {"concepts": [], "people": [], "ready": True}
    cur = lake.cursor()
    try:
        known = set(_people_with(cur, profile))
        cur.register("_query", _expanded(wanted, _vocabulary(cur, vocabulary), known))
        rows = cur.execute(
            """
            WITH best AS (  -- each person's best heading for each query concept
              SELECT p.person_id, q.query_ui, q.q,
                     arg_max(p.descriptor_ui, p.weight * q.factor) AS ui,
                     arg_max(p.descriptor, p.weight * q.factor) AS descriptor,
                     arg_max(p.papers, p.weight * q.factor) AS papers,
                     max(p.weight * q.factor) AS w
              FROM read_parquet(?) p JOIN _query q ON q.member_ui = p.descriptor_ui
              GROUP BY p.person_id, q.query_ui, q.q
            )
            SELECT person_id, sum(w * q) AS score,
                   list_slice(list({'descriptor_ui': ui, 'descriptor': descriptor,
                                    'contribution': w * q, 'papers': papers}
                                   ORDER BY w * q DESC), 1, ?) AS matches
            FROM best GROUP BY person_id ORDER BY score DESC, person_id LIMIT ?
            """,
            [str(profile), MATCHES_SHOWN, limit],
        ).fetchall()
    finally:
        cur.close()
    return {
        "concepts": wanted,
        "people": [
            {"id": pid, "score": round(score, 4), "matches": _merged(matches)}
            for pid, score, matches in rows
            if score > 0
        ],
        "ready": True,
    }


def _merged(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per heading: a narrower heading can count toward two query concepts."""
    out: dict[str, dict[str, Any]] = {}
    for m in matches:
        if m["descriptor_ui"] in out:
            out[m["descriptor_ui"]]["contribution"] += m["contribution"]
        else:
            out[m["descriptor_ui"]] = dict(m)
    return sorted(out.values(), key=lambda m: -m["contribution"])


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


def concept_names(
    lake: Lake, path: Path, text: str, limit: int = 10, vocabulary: Path | None = None
) -> list[dict[str, Any]]:
    """Concepts whose name, or one of whose synonyms, contains the text; most widely held
    first (for choosing directly). A broad heading counts everyone beneath it."""
    if not _exists(path) or not text.strip():
        return []
    needle = text.strip().lower()
    cur = lake.cursor()
    try:
        if _exists(vocabulary):
            rows = cur.execute(
                "SELECT descriptor_ui, name, people_below, "
                "list_filter(entry_terms, lambda t: contains(lower(t), ?)) AS terms "
                "FROM read_parquet(?) WHERE people_below > 0 AND (contains(lower(name), ?) "
                "OR len(list_filter(entry_terms, lambda t: contains(lower(t), ?))) > 0)",
                [needle, str(vocabulary), needle, needle],
            ).fetchall()
            exact = {needle, f"{needle}s", needle.removesuffix("s")}

            def rank(row: tuple) -> tuple:
                _, name, n, terms = row
                named = {name.lower(), *(t.lower() for t in terms)}
                return (not (named & exact), -n, name)

            return [
                {
                    "descriptor_ui": ui,
                    "descriptor": name,
                    "people": n,
                    **({"via": terms[0]} if needle not in name.lower() and terms else {}),
                }
                for ui, name, n, terms in sorted(rows, key=rank)[:limit]
            ]
        rows = cur.execute(
            "SELECT descriptor_ui, min(descriptor) AS name, count(*) AS people "
            "FROM read_parquet(?) WHERE contains(lower(descriptor), ?) "
            "GROUP BY 1 ORDER BY people DESC, name LIMIT ?",
            [str(path), needle, limit],
        ).fetchall()
    finally:
        cur.close()
    return [{"descriptor_ui": ui, "descriptor": name, "people": n} for ui, name, n in rows]


__all__ = ["concept_names", "find_experts", "person_concepts", "query_profile", "similar_people"]
