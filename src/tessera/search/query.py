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
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tessera.core.errors import TesseraError
from tessera.search.index import ALL_KINDS, KINDS, has_kind_column, kind_token

MARK_START, MARK_END = "", ""
MAX_TERMS = 12
MAX_QUERY_LENGTH = 500
COLUMN_WEIGHTS = "10.0, 6.0, 3.0, 1.0"  # title, people, terms, body
KINDED_WEIGHTS = COLUMN_WEIGHTS + ", 0.0"  # and the kind, which never adds to the rank
SORTER_THREADS = 4  # for SQLite's sorter: grouping a common word's facets, for example
FACETS_KEPT = 256  # facet counts kept per query, so paging through results does not recount them
PREFIX_MARKS_UP_TO = 200_000  # documents in the index, above which a part word is not marked
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
    "opportunity": (
        "status",
        "eligibility",
        "closing",
        "source",
        "kind",
        "agency",
        "activity",
        "category",
    ),
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


# Words too common to help find papers on a subject when any word may match.
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "this",
        "to",
        "was",
        "were",
        "which",
        "with",
        "will",
        "can",
        "may",
        "new",
        "also",
        "other",
        "such",
        "these",
        "those",
        "than",
        "more",
        "most",
        "using",
        "studies",
        "study",
        "research",
        "award",
        "awards",
        "program",
        "programs",
        "grant",
        "grants",
        "funding",
        "opportunity",
        "notice",
        "applications",
        "application",
        "support",
        "supports",
        "trial",
        "trials",
        "clinical",
    }
)
MAX_ANY_TERMS = 24


def to_fts_any(text: str | None) -> str | None:
    """Turn text into an FTS5 query that matches any of its distinctive words (ranked by how
    many match, and how rare they are), for reading a long text such as an opportunity."""
    if not text:
        return None
    words = []
    for token in re.findall(r"\w+", text[: MAX_QUERY_LENGTH * 20].lower()):
        common = token in STOPWORDS or token.isdigit()
        if len(token) >= 3 and not common and token not in words:
            words.append(token)
    words = words[:MAX_ANY_TERMS]
    return " OR ".join(f'"{w}"' for w in words) if words else None


_facets_kept: OrderedDict[tuple, dict[str, list[dict[str, Any]]]] = OrderedDict()
_facets_lock = threading.Lock()


def _version(path: Path) -> int:
    """The index file's version: rebuilds replace it, and decisions change it in place."""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _copy(facets: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    return {name: [dict(v) for v in values] for name, values in facets.items()}


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
        con.execute(f"PRAGMA threads = {SORTER_THREADS}")
        con.execute("PRAGMA temp_store = MEMORY")  # each search's matches, in ``temp.hits``
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
        clauses, params = SearchIndex._facet_filters(filters, "d.id")
        return " AND ".join(["d.kind = ?", *clauses]), [kind, *params]

    @staticmethod
    def _facet_filters(filters: dict[str, list[str]], ident: str) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for name, values in sorted(filters.items()):
            values = [v for v in values if v]
            if not values:
                continue
            marks = ", ".join("?" for _ in values)
            clauses.append(
                f"{ident} IN (SELECT doc_id FROM facet WHERE name = ? AND value IN ({marks}))"
            )
            params += [name, *values]
        return clauses, params

    def _hits(
        self,
        con: sqlite3.Connection,
        kind: str | None,
        fts: str,
        filters: dict[str, list[str]],
    ) -> None:
        """Every match, with its rank, in the temporary table ``hits`` (with each one's kind, when
        every kind is searched): the full-text query runs once per search, not once for the
        count, again for the page, and again for the facets. With the last word typed in part,
        it merges the lists of every word that begins so, which at a million papers takes most
        of a second.

        From 0.7.0 each document's kind is a word in the full-text index, so one kind's
        matches are found there alone, and an (id, kind) index gives every match's kind without
        reading the documents. An index from before then is read the old way."""
        kinded = has_kind_column(con)
        weights = KINDED_WEIGHTS if kinded else COLUMN_WEIGHTS
        rank = f"doc_fts.rowid AS id, bm25(doc_fts, {weights}) AS rank"
        con.execute("DROP TABLE IF EXISTS temp.hits")
        if kind is None:
            doc = "doc d INDEXED BY ix_doc_kind" if kinded else "doc d"
            con.execute(
                f"CREATE TEMP TABLE hits AS SELECT {rank}, d.kind AS kind FROM doc_fts "
                f"JOIN {doc} ON d.id = doc_fts.rowid WHERE doc_fts MATCH ?",
                [fts],
            )
            con.execute("CREATE INDEX temp.hits_by_kind ON hits (kind, rank, id)")
        elif kinded:
            clauses, params = self._facet_filters(filters, "doc_fts.rowid")
            where = "".join(f" AND {c}" for c in clauses)
            con.execute(
                f"CREATE TEMP TABLE hits AS SELECT {rank} FROM doc_fts "
                f"WHERE doc_fts MATCH ?{where}",
                [f'"{kind_token(kind)}" AND ({fts})', *params],
            )
        else:
            where, params = self._filters(kind, filters)
            con.execute(
                f"CREATE TEMP TABLE hits AS SELECT {rank} FROM doc_fts "
                f"JOIN doc d ON d.id = doc_fts.rowid WHERE doc_fts MATCH ? AND {where}",
                [fts, *params],
            )

    def search(
        self,
        kind: str,
        query: str | None = None,
        filters: dict[str, list[str]] | None = None,
        sort: str | None = None,
        limit: int = 20,
        offset: int = 0,
        with_facets: bool = True,
        match: str = "all",
        with_highlights: bool = True,
    ) -> SearchResult:
        """``match`` "all" needs every word (searches people type); "any" ranks papers by how
        many of the text's distinctive words they have (reading a long text)."""
        if kind not in ALL_KINDS:
            raise TesseraError(f"Unknown kind {kind!r}; choose from {', '.join(ALL_KINDS)}")
        fts = to_fts_any(query) if match == "any" else to_fts_query(query)
        con = self.connect()
        try:
            if fts:
                self._hits(con, kind, fts, filters or {})
                ids_sql, base_params = "SELECT id FROM temp.hits", []
                total = con.execute("SELECT count(*) FROM temp.hits").fetchone()[0]
                order = sort if sort in SORTS[kind] else "relevance"
                if order == "relevance":  # ranked by ID and rank alone; the page read in full
                    ids = [
                        r[0]
                        for r in con.execute(
                            "SELECT id FROM temp.hits ORDER BY rank, id LIMIT ? OFFSET ?",
                            [limit, offset],
                        )
                    ]
                    rows = self._docs(con, ids)
                else:
                    rows = con.execute(
                        f"SELECT d.* FROM doc d WHERE d.id IN ({ids_sql}) "
                        f"ORDER BY {SORTS[kind][order]} LIMIT ? OFFSET ?",
                        [limit, offset],
                    ).fetchall()
            else:
                where, params = self._filters(kind, filters or {})
                base, base_params = f"FROM doc d WHERE {where}", params
                ids_sql = f"SELECT d.id {base}"
                order = sort if sort in SORTS[kind] else DEFAULT_SORT[kind]
                total = con.execute(f"SELECT count(*) {base}", base_params).fetchone()[0]
                rows = con.execute(
                    f"SELECT d.* {base} ORDER BY {SORTS[kind][order]} LIMIT ? OFFSET ?",
                    [*base_params, limit, offset],
                ).fetchall()
            wanted = fts and with_highlights
            marks = self._highlights(con, fts, [r["id"] for r in rows]) if wanted else {}
            facets: dict[str, list[dict[str, Any]]] = {}
            if with_facets:
                unfiltered = not fts and not any(v for v in (filters or {}).values())
                totals = self._facet_totals(con, kind) if unfiltered else None
                facets = (
                    totals
                    if totals is not None
                    else self._counted_facets(con, kind, fts, filters or {}, ids_sql, base_params)
                )
        except sqlite3.OperationalError as exc:  # defensive: input is sanitized above
            raise TesseraError(f"Search failed: {exc}") from exc
        finally:
            con.close()
        return SearchResult([_item(r, marks.get(r["id"])) for r in rows], total, facets)

    @staticmethod
    def _has_totals(con: sqlite3.Connection) -> bool:
        """Indexes built before 0.7.0 have no stored totals (``tessera build search`` adds them)."""
        found = con.execute("SELECT 1 FROM sqlite_master WHERE name = 'facet_total'").fetchone()
        return found is not None

    @classmethod
    def _facet_totals(
        cls, con: sqlite3.Connection, kind: str
    ) -> dict[str, list[dict[str, Any]]] | None:
        """A whole kind's facet counts, as stored when the index was written."""
        if not cls._has_totals(con):
            return None
        names = FACETS[kind]
        marks = ", ".join("?" for _ in names)
        rows = con.execute(
            f"SELECT name, value, n FROM facet_total WHERE kind = ? AND name IN ({marks})",
            [kind, *names],
        ).fetchall()
        return cls._shape(names, [(r["name"], r["value"], r["n"]) for r in rows])

    @staticmethod
    def _shape(names: Any, rows: list[tuple[str, str, int]]) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {name: [] for name in names}
        for name, value, n in rows:
            out[name].append({"value": value, "count": n})
        for name, values in out.items():
            if name == "year":
                values.sort(key=lambda v: v["value"], reverse=True)
            else:
                values.sort(key=lambda v: (-v["count"], v["value"]))
            out[name] = values[:50]
        return out

    @classmethod
    def _facet_counts(
        cls, con: sqlite3.Connection, kind: str, ids_sql: str, params: list[Any]
    ) -> dict[str, list[dict[str, Any]]]:
        """Counted from the matches (``ids_sql`` selects their ``id``), each one's facets read
        from the facet index by document, rather than every document's facets scanned."""
        names = FACETS[kind]
        marks = ", ".join("?" for _ in names)
        rows = con.execute(
            f"SELECT f.name, f.value, count(*) AS n FROM ({ids_sql}) m "
            f"CROSS JOIN facet f ON f.doc_id = m.id WHERE f.name IN ({marks}) "
            "GROUP BY f.name, f.value",
            [*params, *names],
        ).fetchall()
        return cls._shape(names, [(r["name"], r["value"], r["n"]) for r in rows])

    def _counted_facets(
        self,
        con: sqlite3.Connection,
        kind: str,
        fts: str | None,
        filters: dict[str, list[str]],
        ids_sql: str,
        params: list[Any],
    ) -> dict[str, list[dict[str, Any]]]:
        """Facet counts of a search, kept until the index changes: the next page of the same
        search does not count them again (a common word's are most of its time)."""
        chosen = tuple(sorted((k, tuple(sorted(v))) for k, v in filters.items() if v))
        key = (str(self.path), _version(self.path), kind, fts, chosen)
        with _facets_lock:
            if key in _facets_kept:
                _facets_kept.move_to_end(key)
                return _copy(_facets_kept[key])
        facets = self._facet_counts(con, kind, ids_sql, params)
        with _facets_lock:
            _facets_kept[key] = _copy(facets)
            while len(_facets_kept) > FACETS_KEPT:
                _facets_kept.popitem(last=False)
        return facets

    @staticmethod
    def _docs(con: sqlite3.Connection, ids: list[int]) -> list[sqlite3.Row]:
        """These documents in full, in the order given."""
        if not ids:
            return []
        marks = ", ".join("?" for _ in ids)
        found = {r["id"]: r for r in con.execute(f"SELECT * FROM doc WHERE id IN ({marks})", ids)}
        return [found[i] for i in ids if i in found]

    def grouped(self, query: str, per_kind: int = 5) -> dict[str, SearchResult]:
        """Top results of every kind, for an "everything" search (members' records only, never
        candidates): the matches found once, then counted and ranked kind by kind."""
        fts = to_fts_query(query)
        results = {kind: SearchResult([], 0) for kind in KINDS}
        if not fts:
            return results
        con = self.connect()
        try:
            self._hits(con, None, fts, {})
            found: dict[str, list[sqlite3.Row]] = {}
            for kind in KINDS:
                count = "SELECT count(*) FROM temp.hits WHERE kind = ?"
                results[kind].total = con.execute(count, [kind]).fetchone()[0]
                if results[kind].total:
                    top = "SELECT id FROM temp.hits WHERE kind = ? ORDER BY rank, id LIMIT ?"
                    found[kind] = self._docs(
                        con, [r[0] for r in con.execute(top, [kind, per_kind])]
                    )
            ids = [r["id"] for rows in found.values() for r in rows]
            marks = self._highlights(con, fts, ids)
        except sqlite3.OperationalError as exc:
            raise TesseraError(f"Search failed: {exc}") from exc
        finally:
            con.close()
        for kind, rows in found.items():
            results[kind].items = [_item(r, marks.get(r["id"])) for r in rows]
        return results

    @staticmethod
    def _highlights(con: sqlite3.Connection, fts: str, ids: list[int]) -> dict[int, sqlite3.Row]:
        """Marks in each result's title, people, and text. A word typed in part is marked
        wherever a word begins so, unless the index holds more than ``PREFIX_MARKS_UP_TO``
        documents: then only whole words are marked, since finding the partial word's place in
        each result merges every word in the index that begins so (about 45 ms a result for
        "cells" among a million papers, whatever the search matched)."""
        if not ids:
            return {}
        if fts.endswith("*"):
            size = con.execute("SELECT max(id) FROM doc").fetchone()[0] or 0
            if size > PREFIX_MARKS_UP_TO:
                fts = fts[:-1]
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
            if self._has_totals(con):
                rows = con.execute(
                    "SELECT value, n FROM facet_total WHERE kind = ? AND name = ? ORDER BY value",
                    [kind, name],
                ).fetchall()
            else:
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
