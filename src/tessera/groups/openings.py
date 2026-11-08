"""Openings: jobs and training opportunities a group posts (LAB-08).

An opening is open from its posting date until its closing date has passed, so nothing has to
remember to take it down; an editor can also close it early, or extend it. Closed openings
stay in the group's history and its editors' lists, never on its page.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection

from tessera.core.clock import utc_now
from tessera.core.ids import new_id
from tessera.groups.store import GroupError, GroupNotFound, _event, get_group
from tessera.ledger.tables import opening

KINDS = {"job": "Job", "training": "Training", "other": "Opportunity"}
FIELDS = ("title", "kind", "description", "url", "contact", "posted_on", "closes_on")
MAX_OPEN_DAYS = 730  # an opening runs at most two years
_URL = re.compile(r"^https?://[^\s/]+\.[^\s]+$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_open(row: dict[str, Any], today: date) -> bool:
    return row["closed_at"] is None and row["posted_on"] <= today <= row["closes_on"]


def view(row: dict[str, Any], today: date) -> dict[str, Any]:
    return {
        "id": row["id"],
        "group_id": row["unit_id"],
        "title": row["title"],
        "kind": row["kind"],
        "kind_label": KINDS.get(row["kind"], row["kind"]),
        "description": row["description"],
        "url": row["url"],
        "contact": row["contact"],
        "posted_on": row["posted_on"].isoformat(),
        "closes_on": row["closes_on"].isoformat(),
        "open": is_open(row, today),
        "closed_early": row["closed_at"] is not None,
        "days_left": (row["closes_on"] - today).days if is_open(row, today) else None,
    }


def _date(value: Any, what: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise GroupError(f"The {what} date is written 2026-11-09.") from exc


def _clean(fields: dict[str, Any], *, partial: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if key not in FIELDS:
            raise GroupError(f"Openings have no {key!r}.")
        if isinstance(value, str):
            value = value.strip()
        if key == "title":
            if not value or len(value) > 200:
                raise GroupError("An opening needs a title of at most 200 characters.")
        elif key == "kind":
            if value not in KINDS:
                raise GroupError("An opening is a job, training, or other opportunity.")
        elif key == "description":
            if value and len(value) > 4000:
                raise GroupError("A description is at most 4,000 characters.")
        elif key == "url":
            if value and (len(value) > 500 or not _URL.match(value)):
                raise GroupError("A link is an address starting with https://.")
        elif key == "contact":
            if value and (len(value) > 320 or not _EMAIL.match(value)):
                raise GroupError(f"{value!r} is not an email address.")
        elif key in ("posted_on", "closes_on"):
            value = _date(value, "posting" if key == "posted_on" else "closing")
        out[key] = value if value != "" else None
    if not partial:
        for key in ("title", "closes_on"):
            if not out.get(key):
                raise GroupError(f"An opening needs a {key.replace('_on', ' date')}.")
    return out


def _check_dates(posted: date, closes: date) -> None:
    if closes < posted:
        raise GroupError("An opening closes after it is posted.")
    if closes - posted > timedelta(days=MAX_OPEN_DAYS):
        raise GroupError("An opening runs at most two years; post it again after that.")


def get_opening(conn: Connection, opening_id: str) -> dict[str, Any]:
    row = conn.execute(select(opening).where(opening.c.id == opening_id)).mappings().first()
    if row is None:
        raise GroupNotFound(f"No opening {opening_id!r}.")
    return dict(row)


def list_openings(
    conn: Connection, group_ids: list[str], today: date, *, include_closed: bool = False
) -> list[dict[str, Any]]:
    rows = [
        dict(r)
        for r in conn.execute(
            select(opening)
            .where(opening.c.unit_id.in_(group_ids))
            .order_by(opening.c.closes_on, opening.c.title)
        ).mappings()
    ]
    if not include_closed:
        rows = [r for r in rows if is_open(r, today)]
    return [view(r, today) for r in rows]


def create_opening(
    conn: Connection, group_id: str, fields: dict[str, Any], *, actor: str, today: date
) -> str:
    get_group(conn, group_id)
    values = _clean({"kind": "job", "posted_on": today, **fields}, partial=False)
    _check_dates(values["posted_on"], values["closes_on"])
    now = utc_now()
    opening_id = new_id("opn")
    conn.execute(
        insert(opening).values(
            id=opening_id, unit_id=group_id, created_by=actor, created_at=now, updated_at=now,
            **values,
        )
    )  # fmt: skip
    _event(conn, group_id, actor, "opening_add", opening=opening_id, title=values["title"],
           closes_on=values["closes_on"].isoformat())  # fmt: skip
    return opening_id


def update_opening(
    conn: Connection,
    opening_id: str,
    fields: dict[str, Any],
    *,
    actor: str,
    close: bool | None = None,
) -> dict[str, Any]:
    """Change an opening; ``close`` True closes it now, False opens it again (until its date)."""
    row = get_opening(conn, opening_id)
    values = _clean(fields, partial=True)
    _check_dates(
        values.get("posted_on", row["posted_on"]), values.get("closes_on", row["closes_on"])
    )
    if close is True and row["closed_at"] is None:
        values["closed_at"] = utc_now()
    elif close is False:
        values["closed_at"] = None
    changed = {k: v for k, v in values.items() if row[k] != v}
    if not changed:
        return {}
    conn.execute(
        update(opening).where(opening.c.id == opening_id).values(**changed, updated_at=utc_now())
    )
    action = "opening_close" if changed.get("closed_at") else "opening_edit"
    _event(conn, row["unit_id"], actor, action, opening=opening_id, title=row["title"],
           fields=sorted(changed))  # fmt: skip
    return changed


def expiring(
    conn: Connection, group_ids: list[str], today: date, within: int = 14
) -> list[dict[str, Any]]:
    """Open openings that close within ``within`` days, soonest first."""
    return [
        o
        for o in list_openings(conn, group_ids, today)
        if o["days_left"] is not None and o["days_left"] <= within
    ]


__all__ = [
    "KINDS",
    "create_opening",
    "expiring",
    "get_opening",
    "is_open",
    "list_openings",
    "update_opening",
    "view",
]
