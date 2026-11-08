"""Research themes and projects (LAB-07): what a group works on, in its own words, linking the
people, grants, publications, and studies behind each.

A theme is long-lived ("Memory T cells in barrier tissues"); a project is a piece of work
with an end ("A single-cell atlas of the lung mucosa"). Links are references: people by their
IDs (``per_…``), and records by their keys (``grt:<core project>``, ``pub:<PMID>``,
``stu:<NCT number>``). The group's editors choose them from the group's own people and
records.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Connection

from tessera.core.clock import utc_now
from tessera.core.ids import new_id
from tessera.groups.store import GroupError, GroupNotFound, _event, get_group
from tessera.ledger.tables import person, theme, theme_link

KINDS = {"theme": "Theme", "project": "Project"}
MAX_LINKS = 200
_REF = re.compile(r"^(per_[0-9A-HJKMNP-TV-Z]{26}|grt:[A-Z0-9]{4,20}|pub:\d{1,9}|stu:NCT\d{8})$")


def _clean(fields: dict[str, Any], *, partial: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if key not in ("name", "kind", "summary", "status"):
            raise GroupError(f"Themes have no {key!r}.")
        if isinstance(value, str):
            value = value.strip()
        if key == "name" and (not value or len(value) > 200):
            raise GroupError("A theme needs a name of at most 200 characters.")
        if key == "kind" and value not in KINDS:
            raise GroupError("A theme is a theme or a project.")
        if key == "summary" and value and len(value) > 4000:
            raise GroupError("A summary is at most 4,000 characters.")
        if key == "status" and value not in ("active", "archived"):
            raise GroupError("A theme is active or archived.")
        out[key] = (value or None) if key == "summary" else value
    if not partial and not out.get("name"):
        raise GroupError("A theme needs a name.")
    return out


def check_refs(conn: Connection, refs: list[str]) -> list[str]:
    refs = list(dict.fromkeys(r.strip() for r in refs if r and r.strip()))
    if len(refs) > MAX_LINKS:
        raise GroupError(f"A theme links at most {MAX_LINKS} people and records.")
    bad = [r for r in refs if not _REF.match(r)]
    if bad:
        raise GroupError(f"{bad[0]!r} is not a person or a record reference.")
    people = [r for r in refs if r.startswith("per_")]
    if people:
        found = set(
            conn.execute(
                select(person.c.id).where(
                    person.c.id.in_(people), person.c.status.in_(("active", "former"))
                )
            ).scalars()
        )
        missing = [p for p in people if p not in found]
        if missing:
            raise GroupError(f"{missing[0]!r} is not a person in the directory.")
    return refs


def get_theme(conn: Connection, theme_id: str) -> dict[str, Any]:
    row = conn.execute(select(theme).where(theme.c.id == theme_id)).mappings().first()
    if row is None:
        raise GroupNotFound(f"No theme {theme_id!r}.")
    return dict(row)


ORDER = {"per": 0, "grt": 1, "pub": 2, "stu": 3}  # people, then funding, outputs, studies


def links_of(conn: Connection, theme_ids: list[str]) -> dict[str, list[str]]:
    rows: dict[str, list[tuple[Any, ...]]] = {t: [] for t in theme_ids}
    for theme_id, ref, added in conn.execute(
        select(theme_link.c.theme_id, theme_link.c.ref, theme_link.c.added_at).where(
            theme_link.c.theme_id.in_(theme_ids)
        )
    ):
        rows[theme_id].append((ORDER.get(ref[:3], 9), added, ref))
    return {t: [ref for *_, ref in sorted(found)] for t, found in rows.items()}


def list_themes(
    conn: Connection, group_id: str, *, include_archived: bool = False
) -> list[dict[str, Any]]:
    query = select(theme).where(theme.c.unit_id == group_id).order_by(theme.c.kind, theme.c.name)
    if not include_archived:
        query = query.where(theme.c.status == "active")
    rows = [dict(r) for r in conn.execute(query).mappings()]
    links = links_of(conn, [r["id"] for r in rows])
    return [
        {
            "id": r["id"],
            "group_id": r["unit_id"],
            "name": r["name"],
            "kind": r["kind"],
            "kind_label": KINDS[r["kind"]],
            "summary": r["summary"],
            "status": r["status"],
            "links": links[r["id"]],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


def _set_links(conn: Connection, theme_id: str, refs: list[str], actor: str) -> None:
    current = set(
        conn.execute(select(theme_link.c.ref).where(theme_link.c.theme_id == theme_id)).scalars()
    )
    drop = current - set(refs)
    if drop:
        conn.execute(
            delete(theme_link).where(
                theme_link.c.theme_id == theme_id, theme_link.c.ref.in_(sorted(drop))
            )
        )
    now = utc_now()
    for ref in refs:
        if ref not in current:
            conn.execute(
                insert(theme_link).values(theme_id=theme_id, ref=ref, added_by=actor, added_at=now)
            )


def create_theme(
    conn: Connection,
    group_id: str,
    fields: dict[str, Any],
    *,
    actor: str,
    links: list[str] | None = None,
) -> str:
    get_group(conn, group_id)
    values = _clean({"kind": "theme", **fields}, partial=False)
    refs = check_refs(conn, links or [])
    now = utc_now()
    theme_id = new_id("thm")
    conn.execute(
        insert(theme).values(
            id=theme_id, unit_id=group_id, created_by=actor, created_at=now, updated_at=now,
            **values,
        )
    )  # fmt: skip
    _set_links(conn, theme_id, refs, actor)
    _event(conn, group_id, actor, "theme_add", theme=theme_id, name=values["name"])
    return theme_id


def update_theme(
    conn: Connection,
    theme_id: str,
    fields: dict[str, Any],
    *,
    actor: str,
    links: list[str] | None = None,
) -> None:
    row = get_theme(conn, theme_id)
    values = _clean(fields, partial=True)
    changed = {k: v for k, v in values.items() if row[k] != v}
    if changed:
        conn.execute(
            update(theme).where(theme.c.id == theme_id).values(**changed, updated_at=utc_now())
        )
    if links is not None:
        _set_links(conn, theme_id, check_refs(conn, links), actor)
        conn.execute(update(theme).where(theme.c.id == theme_id).values(updated_at=utc_now()))
    if changed or links is not None:
        _event(conn, row["unit_id"], actor, "theme_edit", theme=theme_id, name=row["name"],
               fields=sorted(changed) + (["links"] if links is not None else []))  # fmt: skip


__all__ = ["KINDS", "check_refs", "create_theme", "get_theme", "list_themes", "update_theme"]
