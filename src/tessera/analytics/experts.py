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


def _singular(word: str) -> str:
    """A plural read as its singular, so that "germinal centers" names "Germinal Center"."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _phrase(words: list[str]) -> str:
    return " ".join(_singular(w) for w in words)


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


class ConceptReader:
    """What the organization's concept profiles and MeSH's vocabulary know, loaded once, to
    read questions (or funding opportunities) as concepts and match people to them."""

    def __init__(self, lake: Lake, profile: Path, vocabulary: Path | None = None) -> None:
        self.lake, self.profile = lake, profile
        cur = lake.cursor()
        try:
            self.known = _people_with(cur, profile)
            self.population = cur.execute(
                "SELECT count(DISTINCT person_id) FROM read_parquet(?)", [str(profile)]
            ).fetchall()[0][0]
            self.vocab_rows = _vocabulary(cur, vocabulary)
        finally:
            cur.close()
        vocab = {v["descriptor_ui"]: v for v in self.vocab_rows if v["people_below"]}
        # A heading can be asked for if someone holds it or anything beneath it.
        self.holders = {ui: n for ui, (_, n) in self.known.items()}
        for ui, v in vocab.items():
            self.holders[ui] = max(self.holders.get(ui, 0), v["people_below"])
        self.name_of = {ui: name for ui, (name, _) in self.known.items()} | {
            ui: v["name"] for ui, v in vocab.items()
        }
        # Phrases that name a heading or one of its synonyms.
        self.terms: dict[str, str] = {}
        for ui, name in self.name_of.items():
            self.terms.setdefault(_phrase(_words(name)), ui)
        for ui, v in vocab.items():
            for term in v["entry_terms"] or []:
                self.terms.setdefault(_phrase(_words(term)), ui)

    def idf(self, ui: str) -> float:
        return math.log((self.population + 1) / self.holders[ui])

    def read(
        self,
        index: SearchIndex,
        q: str | None,
        concepts: list[str],
        papers_by: str | None = None,
        match: str = "all",
    ) -> list[dict[str, Any]]:
        """The concepts a text stands for, with weights (unit length): the headings and
        synonyms it names, and the headings of the organization's papers that ``papers_by``
        (the text itself, unless given) finds."""
        weights: Counter = Counter()
        via: dict[str, str] = {}
        named: set[str] = set()  # concepts the query names outright, or that were chosen
        for ui in concepts:
            ui = ui.removeprefix("msh:")
            if ui in self.holders:
                weights[ui] += 3.0 * self.idf(ui)
                named.add(ui)
        if q and q.strip():
            words = _words(q)
            singular = [_singular(w) for w in words]
            used = [False] * len(words)
            for size in range(min(LONGEST_PHRASE, len(words)), 0, -1):
                for i in range(len(words) - size + 1):
                    if any(used[i : i + size]):
                        continue
                    phrase = " ".join(singular[i : i + size])
                    ui = self.terms.get(phrase)
                    if ui and ui in self.holders and self.name_of[ui] not in CHECK_TAGS:
                        weights[ui] += 3.0 * self.idf(ui)
                        named.add(ui)
                        if phrase != _phrase(_words(self.name_of[ui])):
                            via[ui] = " ".join(words[i : i + size])
                        used[i : i + size] = [True] * size
            finder = q if papers_by is None else papers_by
            hits = index.search("publication", finder, None, None, QUERY_PAPERS, 0,
                                with_facets=False, match=match, with_highlights=False)  # fmt: skip
            ranked = [item["ref"].removeprefix("pub:") for item in hits.items]
            if ranked:
                rank = {pmid: i for i, pmid in enumerate(ranked)}
                cur = self.lake.cursor()
                try:
                    cur.register("_hits", pa.table({"pmid": pa.array(ranked, pa.string())}))
                    found = cur.execute(
                        "SELECT pmid, descriptor_ui, descriptor "
                        "FROM pubmed_mesh JOIN _hits USING (pmid)"
                    ).fetchall()
                finally:
                    cur.close()
                for pmid, ui, descriptor in found:
                    if ui in self.known and descriptor not in CHECK_TAGS:
                        weights[ui] += self.idf(ui) / (1 + rank[pmid] / 50)  # earlier count more
        # What the query names leads what its papers suggest, however broad the heading.
        others = max((w for ui, w in weights.items() if ui not in named), default=0.0)
        for ui in named:
            weights[ui] = max(weights[ui], 1.2 * others)
        top = weights.most_common(QUERY_CONCEPTS)
        norm = math.sqrt(sum(w * w for _, w in top)) or 1.0
        return [
            {
                "descriptor_ui": ui,
                "descriptor": self.name_of[ui],
                "weight": round(w / norm, 4),
                **({"via": via[ui]} if ui in via else {}),
            }
            for ui, w in top
            if w > 0
        ]

    def rank(self, wanted: list[dict[str, Any]], limit: int = 20,
             cur: Any = None, source: str | None = None) -> list[dict[str, Any]]:  # fmt: skip
        """People whose profiles match these concepts, best first, each with the headings
        that match. ``source`` is a table already holding the profiles (for many queries)."""
        if not wanted:
            return []
        own = cur is None
        cur = cur or self.lake.cursor()
        try:
            cur.register("_query", _expanded(wanted, self.vocab_rows, set(self.known)))
            table = source or "read_parquet(?)"
            params = ([] if source else [str(self.profile)]) + [MATCHES_SHOWN, limit]
            rows = cur.execute(
                f"""
                WITH best AS (  -- each person's best heading for each query concept
                  SELECT p.person_id, q.query_ui, q.q,
                         arg_max(p.descriptor_ui, p.weight * q.factor) AS ui,
                         arg_max(p.descriptor, p.weight * q.factor) AS descriptor,
                         arg_max(p.papers, p.weight * q.factor) AS papers,
                         max(p.weight * q.factor) AS w
                  FROM {table} p JOIN _query q ON q.member_ui = p.descriptor_ui
                  GROUP BY p.person_id, q.query_ui, q.q
                )
                SELECT person_id, sum(w * q) AS score,
                       list_slice(list({{'descriptor_ui': ui, 'descriptor': descriptor,
                                        'contribution': w * q, 'papers': papers}}
                                       ORDER BY w * q DESC), 1, ?) AS matches
                FROM best GROUP BY person_id ORDER BY score DESC, person_id LIMIT ?
                """,  # noqa: S608 - the table is ours
                params,
            ).fetchall()
            cur.unregister("_query")
        finally:
            if own:
                cur.close()
        return [
            {"id": pid, "score": round(score, 4), "matches": _merged(matches)}
            for pid, score, matches in rows
            if score > 0
        ]


def query_profile(
    lake: Lake,
    index: SearchIndex,
    profile: Path,
    q: str | None,
    concepts: list[str],
    vocabulary: Path | None = None,
) -> list[dict[str, Any]]:
    """The concepts a query stands for, with weights (unit length)."""
    return ConceptReader(lake, profile, vocabulary).read(index, q, concepts)


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
    reader = ConceptReader(lake, profile, vocabulary)
    wanted = reader.read(index, q, concepts)
    return {"concepts": wanted, "people": reader.rank(wanted, limit), "ready": True}


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
