"""Saved searches (DIS-10): a search someone wants to ask again, and what is new since they
last looked.

A saved search keeps its words and filters, and the records it matched when its owner last
looked. Asking again finds the matches now; those not seen before are new. Saved searches
belong to one person and nobody else sees them.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Connection

from tessera.core.clock import utc_now
from tessera.core.errors import TesseraError
from tessera.core.ids import new_id
from tessera.ledger.tables import saved_search
from tessera.search.index import KINDS
from tessera.search.query import SearchIndex

MAX_MATCHES = 5000  # matches remembered; a search that finds more shows counts only
MAX_SAVED = 100  # per person
PAGE = 200
FILTER_NAMES = frozenset(
    {"status", "unit", "year", "journal", "type", "flag", "activity", "institute", "phase",
     "person", "kind", "closing", "agency", "category", "source"}
)  # fmt: skip


class SavedSearchError(TesseraError):
    """A saved search that cannot be made or found."""


def clean_filters(filters: Any) -> dict[str, list[str]]:
    if not filters:
        return {}
    if not isinstance(filters, dict):
        raise SavedSearchError("Filters are names with lists of values.")
    out: dict[str, list[str]] = {}
    for name, values in filters.items():
        if name not in FILTER_NAMES:
            raise SavedSearchError(f"There is no filter {name!r}.")
        values = [values] if isinstance(values, str) else list(values or [])
        kept = sorted({str(v).strip()[:200] for v in values if str(v).strip()})
        if kept:
            out[name] = kept[:50]
    return out


def matches(index: SearchIndex, kind: str, q: str, filters: dict[str, list[str]]) -> tuple[
    list[str], int
]:  # fmt: skip
    """The refs a search matches now (up to MAX_MATCHES) and how many there are in all."""
    kinds = KINDS if kind == "everything" else (kind,)
    refs: list[str] = []
    total = 0
    for k in kinds:
        offset = 0
        while True:
            result = index.search(k, q or None, filters or None, None, PAGE, offset,
                                  with_facets=False)  # fmt: skip
            if offset == 0:
                total += result.total
            refs += [item["ref"] for item in result.items]
            offset += PAGE
            if offset >= result.total or len(refs) >= MAX_MATCHES:
                break
    return refs[:MAX_MATCHES], total


def _check(kind: str, q: str, name: str) -> None:
    if kind not in ("everything", *KINDS):
        raise SavedSearchError(f"Search people, publications, grants, studies, opportunities, "
                               f"or everything, not {kind!r}.")  # fmt: skip
    if not (name or "").strip() or len(name.strip()) > 200:
        raise SavedSearchError("A saved search's name has 1 to 200 characters.")
    if len(q or "") > 500:
        raise SavedSearchError("Search for at most 500 characters.")


def create(
    conn: Connection, index: SearchIndex, *, owner: str, name: str, kind: str, q: str,
    filters: Any,
) -> str:  # fmt: skip
    _check(kind, q, name)
    filters = clean_filters(filters)
    held = conn.execute(select(saved_search.c.id).where(saved_search.c.owner == owner)).all()
    if len(held) >= MAX_SAVED:
        raise SavedSearchError(f"Keep at most {MAX_SAVED} saved searches; delete one first.")
    refs, _ = matches(index, kind, q, filters)
    search_id = new_id("srh")
    now = utc_now()
    conn.execute(
        insert(saved_search).values(
            id=search_id, owner=owner, name=name.strip(), kind=kind, q=(q or "").strip(),
            filters=filters, seen=sorted(refs), seen_at=now, created_at=now, updated_at=now,
        )
    )  # fmt: skip
    return search_id


def get(conn: Connection, search_id: str, owner: str) -> dict[str, Any]:
    row = conn.execute(
        select(saved_search).where(saved_search.c.id == search_id, saved_search.c.owner == owner)
    ).mappings().first()  # fmt: skip
    if row is None:
        raise SavedSearchError(f"No saved search {search_id!r}.")
    return dict(row)


def list_for(conn: Connection, owner: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        select(saved_search).where(saved_search.c.owner == owner).order_by(saved_search.c.name)
    ).mappings()  # fmt: skip
    return [dict(r) for r in rows]


def check(index: SearchIndex, row: dict[str, Any]) -> dict[str, Any]:
    """The search now: how many it matches, and which were not there when last seen."""
    refs, total = matches(index, row["kind"], row["q"], row["filters"] or {})
    seen = set(row["seen"] or [])
    new = [r for r in refs if r not in seen]
    return {"total": total, "new": new, "remembered": len(refs) < MAX_MATCHES}


def mark_seen(conn: Connection, index: SearchIndex, search_id: str, owner: str) -> None:
    row = get(conn, search_id, owner)
    refs, _ = matches(index, row["kind"], row["q"], row["filters"] or {})
    now = utc_now()
    conn.execute(
        update(saved_search).where(saved_search.c.id == search_id)
        .values(seen=sorted(refs), seen_at=now, updated_at=now)
    )  # fmt: skip


def rename(conn: Connection, search_id: str, owner: str, name: str) -> None:
    row = get(conn, search_id, owner)
    _check(row["kind"], row["q"], name)
    conn.execute(
        update(saved_search).where(saved_search.c.id == search_id)
        .values(name=name.strip(), updated_at=utc_now())
    )  # fmt: skip


def remove(conn: Connection, search_id: str, owner: str) -> None:
    get(conn, search_id, owner)
    conn.execute(delete(saved_search).where(saved_search.c.id == search_id))


__all__ = [
    "MAX_MATCHES",
    "SavedSearchError",
    "check",
    "clean_filters",
    "create",
    "get",
    "list_for",
    "mark_seen",
    "matches",
    "remove",
    "rename",
]
