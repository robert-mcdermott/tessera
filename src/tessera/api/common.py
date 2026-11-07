"""Shared API plumbing: envelopes, cursors, field selection, and record summaries.

Conventions (spec/waves/wave-1.md, slice 4):

* Every successful response is ``{"data": ..., "meta": {...}}``.
* Lists take ``limit`` and an opaque ``cursor``; ``meta.next_cursor`` is null at the end.
* ``fields=a,b`` returns only those keys of each list item (sparse field sets).
* Errors are RFC 9457 problem details (see ``tessera.api.problems``).
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import pyarrow as pa
from fastapi import HTTPException, Query, Request
from sqlalchemy.engine import Engine

from tessera.config import Config
from tessera.lake import Lake
from tessera.search.index import index_path
from tessera.search.query import SearchIndex

MAX_LIMIT = 200

log = logging.getLogger("tessera.api")


def config_of(request: Request) -> Config:
    return request.app.state.config


def engine_of(request: Request) -> Engine:
    return request.app.state.engine


def lake_of(request: Request) -> Lake:
    return request.app.state.lake


def index_of(request: Request) -> SearchIndex:
    return SearchIndex(index_path(request.app.state.home))


@dataclass
class Page:
    limit: int
    offset: int

    def next_cursor(self, total: int) -> str | None:
        following = self.offset + self.limit
        return encode_cursor(following) if following < total else None


def refresh_index(
    request: Request, *, people: Iterable[str] = (), records: Iterable[str] = ()
) -> None:
    """Update the search documents a decision touched. The ledger is the source of truth, so
    a failure here is logged and the next full build repairs it; the decision stands."""
    from tessera.search import update

    try:
        update.apply(
            config_of(request),
            request.app.state.home,
            engine_of(request),
            people=people,
            records=records,
        )
    except Exception:  # never fail a recorded decision over a derived index
        log.warning("search index update failed; the next build will repair it", exc_info=True)


def encode_cursor(offset: int) -> str:
    raw = json.dumps({"o": offset}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        offset = json.loads(base64.urlsafe_b64decode(padded.encode()))["o"]
    except (binascii.Error, ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="The cursor is not valid.") from None
    if not isinstance(offset, int) or offset < 0:
        raise HTTPException(status_code=400, detail="The cursor is not valid.")
    return offset


def page(
    limit: int = Query(20, ge=1, le=MAX_LIMIT, description="Items per page"),
    cursor: str | None = Query(None, description="Opaque cursor from meta.next_cursor"),
    offset: int = Query(0, ge=0, le=1_000_000, description="Items to skip (without a cursor)"),
) -> Page:
    return Page(limit, decode_cursor(cursor) if cursor else offset)


def pick_fields(items: list[dict[str, Any]], fields: str | None) -> list[dict[str, Any]]:
    if not fields:
        return items
    wanted = [f.strip() for f in fields.split(",") if f.strip()]
    return [{k: item[k] for k in wanted if k in item} for item in items]


def envelope(data: Any, **meta: Any) -> dict[str, Any]:
    return {"data": data, "meta": meta}


def split_values(values: Iterable[str] | None) -> list[str]:
    """Accept both ``?x=a&x=b`` and ``?x=a,b``."""
    out: list[str] = []
    for value in values or []:
        out += [v.strip() for v in value.split(",") if v.strip()]
    return out


def band(score: float | None, config: Config) -> str | None:
    if score is None:
        return None
    if score >= config.evidence.strong_threshold:
        return "strong"
    return "possible" if score >= config.evidence.review_threshold else "unlikely"


def assertion_view(row: dict[str, Any], config: Config) -> dict[str, Any]:
    """The public shape of an assertion."""
    return {
        "id": row["id"],
        "subject": {"type": row["subject_type"], "id": row["subject_id"]},
        "predicate": row["predicate"],
        "object": {"type": row["object_type"], "id": row["object_id"]},
        "status": row["status"],
        "origin": row["origin"],
        "method": row["method"],
        "score": row["score"],
        "band": band(row["score"], config),
        "locked": row["locked"],
        "reported": row["reported"],
        "qualifiers": row["qualifiers"] or {},
        "evidence": row["evidence"] or [],
        "decided_by": row["decided_by"],
        "decided_at": row["decided_at"],
        "decision_note": row["decision_note"],
        "first_seen_at": row["first_seen_at"],
        "last_evaluated_at": row["last_evaluated_at"],
    }


def publication_summaries(
    index: SearchIndex, lake: Lake, pmids: list[str]
) -> dict[str, dict[str, Any]]:
    """Summaries by PMID: from the index, falling back to the lake for unindexed papers."""
    try:
        found = {
            ref.removeprefix("pub:"): s
            for ref, s in index.summaries([f"pub:{p}" for p in pmids]).items()
        }
    except Exception:  # the index may not be built yet
        found = {}
    missing = [p for p in pmids if p not in found]
    if missing:
        cur = lake.cursor()
        try:
            cur.register("_pmids", pa.table({"pmid": pa.array(missing, pa.string())}))
            rows = cur.execute(
                "SELECT a.pmid, a.title, coalesce(a.journal_abbrev, a.journal), a.pub_year, "
                "a.pub_date, a.doi, a.is_retracted, "
                "(SELECT count(*) FROM pubmed_author u WHERE u.pmid = a.pmid) "
                "FROM pubmed_article a JOIN _pmids USING (pmid)"
            ).fetchall()
        finally:
            cur.close()
        for pmid, title, journal, year, pub_date, doi, retracted, count in rows:
            found[pmid] = {
                "pmid": pmid,
                "title": title or "(untitled)",
                "journal": journal,
                "year": year,
                "pub_date": pub_date.isoformat() if pub_date else None,
                "authors": None,
                "author_count": count,
                "doi": doi,
                "is_retracted": bool(retracted),
                "citation_count": None,
                "relative_citation_ratio": None,
                "members": [],
            }
    return found


def require(value: Any, what: str) -> Any:
    if value is None:
        raise HTTPException(status_code=404, detail=f"No such {what}.")
    return value
