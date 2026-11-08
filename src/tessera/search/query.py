"""Query the search and serving index safely.

User text is never passed to FTS5 as syntax. It is reduced to words and quoted phrases,
each quoted for FTS5, so operators, column filters, and stray quotes are inert.
Highlights use the private-use characters U+E000 (start) and U+E001 (end); clients escape
the text first and then turn the markers into ``<mark>`` elements.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tessera.core.errors import TesseraError
from tessera.search.index import ALL_KINDS, KINDS

MARK_START, MARK_END = "", ""
MAX_TERMS = 12
MAX_QUERY_LENGTH = 500
COLUMN_WEIGHTS = "10.0, 6.0, 3.0, 1.0"  # title, people, terms, body
SORTS = {
    "person": {"name": "d.sort_key", "activity": "d.weight DESC, d.sort_key"},
    "publication": {"year": "d.sort_key", "citations": "d.weight DESC, d.sort_key"},
    "grant": {"year": "d.sort_key", "amount": "d.weight DESC, d.sort_key"},
    "study": {"year": "d.sort_key"},
    "opportunity": {"closes": "d.sort_key", "posted": "d.weight DESC, d.sort_key"},
    "candidate": {"evidence": "d.weight DESC, d.sort_key", "name": "d.sort_key"},
}
DEFAULT_SORT = {
    "person": "name",
    "publication": "year",
    "grant": "year",
    "study": "year",
    "opportunity": "closes",
    "candidate": "evidence",
}
FACETS = {
    "person": ("status", "unit"),
    "publication": ("year", "journal", "type", "flag"),
    "grant": ("status", "activity", "institute", "year"),
    "study": ("status", "phase", "type", "year"),
    "opportunity": ("status", "source", "kind", "closing", "agency", "activity", "category"),
    "candidate": ("status", "source"),
}


class SearchUnavailable(TesseraError):
    """The search index has not been built yet."""


def to_fts_query(text: str | None) -> str | None:
    """Turn user input into a safe FTS5 query, or None if nothing searchable remains."""
    if not text:
        return None
    text = text[:MAX_QUERY_LENGTH].replace("\x00", " ")
    phrases = [
        " ".join(words) for p in re.findall(r'"([^"]*)"', text) if (words := re.findall(r"\w+", p))
    ]
    rest = re.sub(r'"[^"]*"', " ", text).replace('"', " ")  # stray quotes are just spaces
    tokens = re.findall(r"\w+", rest)
    parts = [f'"{p}"' for p in phrases]
    for i, token in enumerate(tokens):
        last = i == len(tokens) - 1 and not text.endswith((" ", '"'))
        parts.append(f'"{token}"' + ("*" if last and len(token) >= 2 else ""))
    parts = parts[:MAX_TERMS]
    return " ".join(parts) if parts else None


@dataclass
class SearchResult:
    items: list[dict[str, Any]]
    total: int
    facets: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


class SearchIndex:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        if not self.path.is_file():
            raise SearchUnavailable(
                "The search index has not been built yet; run `tessera build search` or "
                "`tessera sync`."
            )
        con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con

    def meta(self) -> dict[str, str]:
        con = self.connect()
        try:
            return {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM meta")}
        finally:
            con.close()

    # -- filtering ----------------------------------------------------------------------------

    @staticmethod
    def _filters(kind: str, filters: dict[str, list[str]]) -> tuple[str, list[Any]]:
        clauses, params = ["d.kind = ?"], [kind]
        for name, values in sorted(filters.items()):
            values = [v for v in values if v]
            if not values:
                continue
            marks = ", ".join("?" for _ in values)
            clauses.append(
                f"d.id IN (SELECT doc_id FROM facet WHERE name = ? AND value IN ({marks}))"
            )
            params += [name, *values]
        return " AND ".join(clauses), params

    def search(
        self,
        kind: str,
        query: str | None = None,
        filters: dict[str, list[str]] | None = None,
        sort: str | None = None,
        limit: int = 20,
        offset: int = 0,
        with_facets: bool = True,
    ) -> SearchResult:
        if kind not in ALL_KINDS:
            raise TesseraError(f"Unknown kind {kind!r}; choose from {', '.join(ALL_KINDS)}")
        fts = to_fts_query(query)
        where, params = self._filters(kind, filters or {})
        con = self.connect()
        try:
            if fts:
                base = (
                    "FROM doc_fts JOIN doc d ON d.id = doc_fts.rowid "
                    f"WHERE doc_fts MATCH ? AND {where}"
                )
                base_params = [fts, *params]
                order = sort if sort in SORTS[kind] else "relevance"
                order_sql = (
                    f"bm25(doc_fts, {COLUMN_WEIGHTS})"
                    if order == "relevance"
                    else SORTS[kind][order]
                )
            else:
                base, base_params = f"FROM doc d WHERE {where}", params
                order = sort if sort in SORTS[kind] else DEFAULT_SORT[kind]
                order_sql = SORTS[kind][order]
            total = con.execute(f"SELECT count(*) {base}", base_params).fetchone()[0]
            rows = con.execute(
                f"SELECT d.* {base} ORDER BY {order_sql} LIMIT ? OFFSET ?",
                [*base_params, limit, offset],
            ).fetchall()
            marks = self._highlights(con, fts, [r["id"] for r in rows]) if fts else {}
            facets = self._facet_counts(con, kind, base, base_params) if with_facets else {}
        except sqlite3.OperationalError as exc:  # defensive: input is sanitized above
            raise TesseraError(f"Search failed: {exc}") from exc
        finally:
            con.close()
        return SearchResult([_item(r, marks.get(r["id"])) for r in rows], total, facets)

    @staticmethod
    def _facet_counts(
        con: sqlite3.Connection, kind: str, base: str, params: list[Any]
    ) -> dict[str, list[dict[str, Any]]]:
        names = FACETS[kind]
        marks = ", ".join("?" for _ in names)
        rows = con.execute(
            f"SELECT f.name, f.value, count(*) AS n FROM facet f WHERE f.name IN ({marks}) "
            f"AND f.doc_id IN (SELECT d.id {base}) GROUP BY f.name, f.value",
            [*names, *params],
        ).fetchall()
        out: dict[str, list[dict[str, Any]]] = {name: [] for name in names}
        for r in rows:
            out[r["name"]].append({"value": r["value"], "count": r["n"]})
        for name, values in out.items():
            if name == "year":
                values.sort(key=lambda v: v["value"], reverse=True)
            else:
                values.sort(key=lambda v: (-v["count"], v["value"]))
            out[name] = values[:50]
        return out

    def grouped(self, query: str, per_kind: int = 5) -> dict[str, SearchResult]:
        """Top results of every kind, for an "everything" search."""
        fts = to_fts_query(query)
        results = {kind: SearchResult([], 0) for kind in KINDS}
        if not fts:
            return results
        kinds = ", ".join("?" for _ in KINDS)  # members' records only, never candidates
        con = self.connect()
        try:
            # FTS5 ranking functions cannot sit inside a window function, so rank first.
            rows = con.execute(
                "WITH hits AS MATERIALIZED ("
                f"SELECT doc_fts.rowid AS id, bm25(doc_fts, {COLUMN_WEIGHTS}) AS rank "
                "FROM doc_fts WHERE doc_fts MATCH ?), "
                "ranked AS (SELECT d.*, row_number() OVER (PARTITION BY d.kind ORDER BY h.rank) "
                "AS rn, count(*) OVER (PARTITION BY d.kind) AS total "
                f"FROM hits h JOIN doc d ON d.id = h.id WHERE d.kind IN ({kinds})) "
                "SELECT * FROM ranked WHERE rn <= ? ORDER BY kind, rn",
                [fts, *KINDS, per_kind],
            ).fetchall()
            marks = self._highlights(con, fts, [r["id"] for r in rows])
        except sqlite3.OperationalError as exc:
            raise TesseraError(f"Search failed: {exc}") from exc
        finally:
            con.close()
        for row in rows:
            result = results[row["kind"]]
            result.items.append(_item(row, marks.get(row["id"])))
            result.total = row["total"]
        return results

    @staticmethod
    def _highlights(con: sqlite3.Connection, fts: str, ids: list[int]) -> dict[int, sqlite3.Row]:
        if not ids:
            return {}
        marks = ", ".join("?" for _ in ids)
        rows = con.execute(
            "SELECT rowid AS id, highlight(doc_fts, 0, ?, ?) AS hl_title, "
            "snippet(doc_fts, 3, ?, ?, '…', 20) AS hl_body, "
            "snippet(doc_fts, 1, ?, ?, '…', 12) AS hl_people "
            f"FROM doc_fts WHERE doc_fts MATCH ? AND rowid IN ({marks})",
            [MARK_START, MARK_END] * 3 + [fts, *ids],
        ).fetchall()
        return {r["id"]: r for r in rows}

    def summaries(self, refs: list[str]) -> dict[str, dict[str, Any]]:
        """Display summaries for known refs (missing refs are simply absent)."""
        out: dict[str, dict[str, Any]] = {}
        if not refs:
            return out
        con = self.connect()
        try:
            for start in range(0, len(refs), 500):
                chunk = refs[start : start + 500]
                marks = ", ".join("?" for _ in chunk)
                for r in con.execute(f"SELECT ref, summary FROM doc WHERE ref IN ({marks})", chunk):
                    out[r["ref"]] = json.loads(r["summary"])
        finally:
            con.close()
        return out

    def counts(self) -> dict[str, int]:
        con = self.connect()
        try:
            return {
                r["kind"]: r["n"]
                for r in con.execute("SELECT kind, count(*) AS n FROM doc GROUP BY kind")
            }
        finally:
            con.close()

    def facet_values(self, kind: str, name: str) -> list[dict[str, Any]]:
        con = self.connect()
        try:
            rows = con.execute(
                "SELECT f.value, count(*) AS n FROM facet f JOIN doc d ON d.id = f.doc_id "
                "WHERE d.kind = ? AND f.name = ? GROUP BY f.value ORDER BY f.value",
                [kind, name],
            ).fetchall()
        finally:
            con.close()
        return [{"value": r["value"], "count": r["n"]} for r in rows]

    def facet_refs(self, kind: str, name: str) -> dict[str, set[str]]:
        """Every value of one facet, with the refs that carry it (for example, unit members)."""
        con = self.connect()
        try:
            rows = con.execute(
                "SELECT f.value, d.ref FROM facet f JOIN doc d ON d.id = f.doc_id "
                "WHERE d.kind = ? AND f.name = ?",
                [kind, name],
            ).fetchall()
        finally:
            con.close()
        out: dict[str, set[str]] = {}
        for r in rows:
            out.setdefault(r["value"], set()).add(r["ref"])
        return out

    def refs_with(self, kind: str, name: str, value: str) -> set[str]:
        """The refs of one kind that carry a facet value (for example, people in a unit)."""
        con = self.connect()
        try:
            rows = con.execute(
                "SELECT d.ref FROM facet f JOIN doc d ON d.id = f.doc_id "
                "WHERE d.kind = ? AND f.name = ? AND f.value = ?",
                [kind, name, value],
            ).fetchall()
        finally:
            con.close()
        return {r["ref"] for r in rows}


def _item(row: sqlite3.Row, marks: sqlite3.Row | None = None) -> dict[str, Any]:
    highlights = {}
    if marks is not None:
        highlights = {
            name: marks[col]
            for name, col in (("title", "hl_title"), ("body", "hl_body"), ("people", "hl_people"))
            if marks[col] and MARK_START in marks[col]
        }
    return {
        "kind": row["kind"],
        "ref": row["ref"],
        "title": row["title"],
        "subtitle": row["subtitle"],
        "year": row["year"],
        "summary": json.loads(row["summary"]),
        "highlights": highlights,
    }
