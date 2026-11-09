"""Each group's page, made of blocks from an approved set (LAB-04), with a draft, publishing,
history, and rollback (LAB-05).

A page is a list of blocks, each ``{"id", "type", "title"?, ...settings}``. Managers arrange
them without code; nothing in a block is HTML or script, and every value is checked here, so a
page can never carry markup into the app or the public site. A group without a published page
shows the default arrangement.

* **Draft:** what the group's editors are working on; saving replaces it.
* **Publish:** the draft becomes the page everyone sees, as a new numbered revision.
* **History:** every published revision is kept.
* **Rollback:** an earlier revision is published again, as a new revision (nothing is lost).
"""

from __future__ import annotations

import re
import secrets
from datetime import date
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Connection

from tessera.core.clock import utc_now
from tessera.groups.store import GroupError, _event, get_group
from tessera.ledger.tables import group_page, group_page_revision

BLOCK_TYPES: dict[str, str] = {
    "about": "About",
    "people": "People",
    "publications": "Publications",
    "grants": "Grants",
    "studies": "Studies",
    "topics": "Topics",
    "connections": "Writes most with",
    "themes": "Themes and projects",
    "openings": "Openings",
    "contact": "Contact",
    "news": "News",
    # The group's approved plain-language summary, where there is one; offered only while the
    # optional assistant is on (see OPTIONAL_TYPES), and shown only then.
    "summary": "Summary",
}
OPTIONAL_TYPES = {"summary": "ai"}  # block type → the configuration section that must be on
REPEATABLE = {"about", "news"}  # text blocks may appear more than once
SIDE = {"topics", "connections", "contact"}  # shown beside the main column on wide screens
DEFAULT = (
    "about", "people", "publications", "themes", "openings", "grants", "studies", "topics",
    "connections", "contact",
)  # fmt: skip
MAX_BLOCKS = 30
MAX_FEATURED = 20
MAX_NEWS = 20
_URL = re.compile(r"^https?://[^\s/]+\.[^\s]+$")
_ID = re.compile(r"^[a-z0-9]{1,16}$")
_PMID = re.compile(r"^\d{1,9}$")


def default_blocks() -> list[dict[str, Any]]:
    return [{"id": t, "type": t} for t in DEFAULT]


def _text(block: dict[str, Any], key: str, limit: int, where: str) -> str | None:
    value = block.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise GroupError(f"{where}: {key} must be text.")
    value = value.replace("\r\n", "\n").strip()
    if len(value) > limit:
        raise GroupError(f"{where}: {key} is at most {limit:,} characters.")
    return value or None


def _int(block: dict[str, Any], key: str, low: int, high: int, where: str) -> int | None:
    value = block.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise GroupError(f"{where}: {key} is a number from {low} to {high}.")
    return value


def validate_blocks(blocks: Any) -> list[dict[str, Any]]:
    """Checked, normalized blocks; raises GroupError naming the first problem."""
    if not isinstance(blocks, list):
        raise GroupError("A page is a list of blocks.")
    if len(blocks) > MAX_BLOCKS:
        raise GroupError(f"A page has at most {MAX_BLOCKS} blocks.")
    out: list[dict[str, Any]] = []
    seen_types: set[str] = set()
    seen_ids: set[str] = set()
    for n, block in enumerate(blocks, start=1):
        where = f"Block {n}"
        if not isinstance(block, dict):
            raise GroupError(f"{where} is not a block.")
        kind = block.get("type")
        if kind not in BLOCK_TYPES:
            raise GroupError(f"{where}: {kind!r} is not one of the blocks a page can have.")
        if kind in seen_types and kind not in REPEATABLE:
            raise GroupError(f"{where}: a page has one {BLOCK_TYPES[kind]} block.")
        seen_types.add(kind)
        block_id = block.get("id") if isinstance(block.get("id"), str) else None
        if not block_id or not _ID.match(block_id) or block_id in seen_ids:
            block_id = secrets.token_hex(4)
        seen_ids.add(block_id)
        clean: dict[str, Any] = {"id": block_id, "type": kind}
        title = _text(block, "title", 120, where)
        if title:
            clean["title"] = title
        known = {"id", "type", "title"}
        if kind in ("about", "contact"):
            known |= {"text"}
            text = _text(block, "text", 6000 if kind == "about" else 1000, where)
            if text:
                clean["text"] = text
        elif kind == "people":
            known |= {"show_alumni"}
            if "show_alumni" in block:
                clean["show_alumni"] = bool(block["show_alumni"])
        elif kind == "publications":
            known |= {"featured", "limit", "show_chart"}
            featured = block.get("featured") or []
            if not isinstance(featured, list) or len(featured) > MAX_FEATURED:
                raise GroupError(f"{where}: at most {MAX_FEATURED} featured publications.")
            pmids = [str(p).strip() for p in featured]
            if any(not _PMID.match(p) for p in pmids):
                raise GroupError(f"{where}: featured publications are PubMed IDs.")
            if pmids:
                clean["featured"] = list(dict.fromkeys(pmids))
            limit = _int(block, "limit", 1, 50, where)
            if limit:
                clean["limit"] = limit
            if "show_chart" in block:
                clean["show_chart"] = bool(block["show_chart"])
        elif kind in ("grants", "studies"):
            known |= {"limit"}
            limit = _int(block, "limit", 1, 50, where)
            if limit:
                clean["limit"] = limit
        elif kind == "news":
            known |= {"items"}
            items = block.get("items") or []
            if not isinstance(items, list) or len(items) > MAX_NEWS:
                raise GroupError(f"{where}: at most {MAX_NEWS} news items.")
            clean["items"] = [_news(item, f"{where}, item {i}") for i, item in enumerate(items, 1)]
        unknown = set(block) - known
        if unknown:
            raise GroupError(f"{where}: {BLOCK_TYPES[kind]} blocks have no {sorted(unknown)[0]!r}.")
        out.append(clean)
    return out


def _news(item: Any, where: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise GroupError(f"{where} is not a news item.")
    unknown = set(item) - {"date", "title", "text", "url"}
    if unknown:
        raise GroupError(f"{where}: news items have no {sorted(unknown)[0]!r}.")
    title = _text(item, "title", 200, where)
    if not title:
        raise GroupError(f"{where} needs a title.")
    out: dict[str, Any] = {"title": title}
    when = _text(item, "date", 10, where)
    if when:
        try:
            out["date"] = date.fromisoformat(when).isoformat()
        except ValueError as exc:
            raise GroupError(f"{where}: the date is written 2026-11-09.") from exc
    text = _text(item, "text", 2000, where)
    if text:
        out["text"] = text
    url = _text(item, "url", 500, where)
    if url:
        if not _URL.match(url):
            raise GroupError(f"{where}: a link is an address starting with https://.")
        out["url"] = url
    return out


def get_page(conn: Connection, group_id: str) -> dict[str, Any] | None:
    row = conn.execute(select(group_page).where(group_page.c.unit_id == group_id)).mappings()
    found = row.first()
    return dict(found) if found else None


def _ensure(conn: Connection, group_id: str) -> dict[str, Any]:
    get_group(conn, group_id)
    page = get_page(conn, group_id)
    if page is None:
        conn.execute(insert(group_page).values(unit_id=group_id, revision=0))
        page = get_page(conn, group_id)
    assert page is not None
    return page


def save_draft(conn: Connection, group_id: str, blocks: Any, *, actor: str) -> list[dict]:
    clean = validate_blocks(blocks)
    _ensure(conn, group_id)
    conn.execute(
        update(group_page)
        .where(group_page.c.unit_id == group_id)
        .values(draft=clean, draft_updated_at=utc_now(), draft_updated_by=actor)
    )
    _event(conn, group_id, actor, "page_draft", blocks=len(clean))
    return clean


def discard_draft(conn: Connection, group_id: str, *, actor: str) -> None:
    page = get_page(conn, group_id)
    if page is None or page["draft"] is None:
        return
    conn.execute(
        update(group_page)
        .where(group_page.c.unit_id == group_id)
        .values(draft=None, draft_updated_at=None, draft_updated_by=None)
    )
    _event(conn, group_id, actor, "page_discard")


def _publish(
    conn: Connection, group_id: str, blocks: list[dict], *, actor: str, action: str,
    note: str | None, **detail: Any,
) -> int:  # fmt: skip
    page = _ensure(conn, group_id)
    number = (
        conn.execute(
            select(func.max(group_page_revision.c.number)).where(
                group_page_revision.c.unit_id == group_id
            )
        ).scalar()
        or 0
    ) + 1
    now = utc_now()
    conn.execute(
        insert(group_page_revision).values(
            unit_id=group_id, number=number, blocks=blocks, action=action, actor=actor,
            note=note, at=now,
        )
    )  # fmt: skip
    values: dict[str, Any] = {
        "published": blocks, "revision": number, "published_at": now, "published_by": actor,
    }  # fmt: skip
    if action == "publish":
        values |= {"draft": None, "draft_updated_at": None, "draft_updated_by": None}
    conn.execute(update(group_page).where(group_page.c.unit_id == group_id).values(**values))
    _event(conn, group_id, actor, f"page_{action}", revision=number, note=note,
           previous=page["revision"], **detail)  # fmt: skip
    return number


def publish(conn: Connection, group_id: str, *, actor: str, note: str | None = None) -> int:
    page = get_page(conn, group_id)
    if page is None or page["draft"] is None:
        raise GroupError("There is no draft to publish; save one first.")
    return _publish(conn, group_id, validate_blocks(page["draft"]), actor=actor,
                    action="publish", note=note)  # fmt: skip


def rollback(
    conn: Connection, group_id: str, number: int, *, actor: str, note: str | None = None
) -> int:
    row = conn.execute(
        select(group_page_revision.c.blocks).where(
            group_page_revision.c.unit_id == group_id, group_page_revision.c.number == number
        )
    ).scalar()
    if row is None:
        raise GroupError(f"This page has no version {number}.")
    return _publish(conn, group_id, validate_blocks(row), actor=actor, action="rollback",
                    note=note, restored=number)  # fmt: skip


def revisions(conn: Connection, group_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        select(group_page_revision)
        .where(group_page_revision.c.unit_id == group_id)
        .order_by(group_page_revision.c.number.desc())
    ).mappings()
    return [dict(r) for r in rows]


def revision_blocks(conn: Connection, group_id: str, number: int) -> list[dict] | None:
    return conn.execute(
        select(group_page_revision.c.blocks).where(
            group_page_revision.c.unit_id == group_id, group_page_revision.c.number == number
        )
    ).scalar()


def published_blocks(conn: Connection, group_id: str) -> tuple[list[dict], bool]:
    """(blocks, whether they are the default arrangement)."""
    page = get_page(conn, group_id)
    if page and page["published"] is not None:
        return list(page["published"]), False
    return default_blocks(), True


__all__ = [
    "BLOCK_TYPES",
    "DEFAULT",
    "OPTIONAL_TYPES",
    "REPEATABLE",
    "SIDE",
    "default_blocks",
    "discard_draft",
    "get_page",
    "publish",
    "published_blocks",
    "revision_blocks",
    "revisions",
    "rollback",
    "save_draft",
    "validate_blocks",
]
