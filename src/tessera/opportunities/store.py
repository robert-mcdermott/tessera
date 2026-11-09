"""Opportunities an organization tracks itself (INT-12): entered in Admin → Opportunities or
imported from CSV or JSON with ``tessera opportunities import``.

An import names each opportunity's ``source`` (who offers or lists it) and, ideally, its
``external_id`` there; importing the same file again updates those opportunities instead of
adding them twice. Without an external ID, the number or else the title stands in for it.
``complete`` archives the opportunities of that source that the file no longer lists.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from tessera.core.clock import utc_now
from tessera.core.errors import TesseraError
from tessera.core.ids import new_id
from tessera.ledger.tables import OPPORTUNITY_KINDS, opportunity
from tessera.opportunities.codes import APPLICANT_TYPES

TEXT_LIMITS = {"source": 200, "external_id": 200, "number": 100, "title": 500, "sponsor": 300,
               "url": 1000, "description": 20000, "eligibility_notes": 5000}  # fmt: skip
DATES = ("posted_on", "closes_on", "internal_deadline")
MONEY = ("award_floor", "award_ceiling")
FIELDS = (*TEXT_LIMITS, "kind", *DATES, *MONEY, "limited_submission", "applicant_types")
MAX_IMPORT = 5000


class OpportunityError(TesseraError):
    """An opportunity that cannot be saved as given."""


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _date(name: str, value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise OpportunityError(f"{name}: {value!r} is not a date (YYYY-MM-DD).") from None


def _money(name: str, value: Any) -> int | None:
    if value in (None, ""):
        return None
    text = re.sub(r"[,$\s]", "", str(value))
    if not text.isdigit():
        raise OpportunityError(f"{name}: {value!r} is not a whole amount in dollars.")
    return int(text)


def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _codes(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    items = value if isinstance(value, list) else re.split(r"[;,|\s]+", str(value))
    codes = [str(c).strip().zfill(2) for c in items if str(c).strip()]
    unknown = [c for c in codes if c not in APPLICANT_TYPES]
    if unknown:
        raise OpportunityError(f"applicant_types: {', '.join(unknown)} are not Grants.gov "
                               "eligibility codes (such as 06, 12, or 20).")  # fmt: skip
    return sorted(set(codes))


def clean(data: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    """Checked values for the fields given (all required ones, unless ``partial``)."""
    unknown = set(data) - set(FIELDS)
    if unknown:
        raise OpportunityError(f"Unknown fields: {', '.join(sorted(unknown))}.")
    out: dict[str, Any] = {}
    for name, limit in TEXT_LIMITS.items():
        if name in data:
            text = _text(data[name])
            if text and len(text) > limit:
                raise OpportunityError(f"{name} has at most {limit:,} characters.")
            out[name] = text
    for name in DATES:
        if name in data:
            out[name] = _date(name, data[name])
    for name in MONEY:
        if name in data:
            out[name] = _money(name, data[name])
    if "kind" in data:
        kind = (_text(data["kind"]) or "foundation").lower()
        if kind not in OPPORTUNITY_KINDS:
            raise OpportunityError(f"kind is one of {', '.join(OPPORTUNITY_KINDS)}.")
        out["kind"] = kind
    if "limited_submission" in data:
        out["limited_submission"] = _flag(data["limited_submission"])
    if "applicant_types" in data:
        out["applicant_types"] = _codes(data["applicant_types"])
    if (not partial or "title" in data) and not out.get("title"):
        raise OpportunityError("An opportunity needs a title.")
    if (not partial or "source" in data) and not out.get("source"):
        raise OpportunityError("An opportunity needs a source: who offers or lists it.")
    url = out.get("url")
    if url and not re.match(r"https?://", url):
        raise OpportunityError("url must start with http:// or https://.")
    floor, ceiling = out.get("award_floor"), out.get("award_ceiling")
    if floor is not None and ceiling is not None and floor > ceiling:
        raise OpportunityError("award_floor is more than award_ceiling.")
    return out


def create(engine: Engine, data: dict[str, Any], actor: str) -> str:
    values = clean(data)
    now = utc_now()
    oid = new_id("opl")
    row = {"kind": "foundation", "limited_submission": False, "applicant_types": [], **values}
    with engine.begin() as conn:
        stamp = {"created_by": actor, "created_at": now, "updated_by": actor, "updated_at": now}
        conn.execute(insert(opportunity).values(id=oid, **row, **stamp))
    return oid


def _row(engine: Engine, oid: str) -> dict[str, Any]:
    with engine.connect() as conn:
        row = conn.execute(select(opportunity).where(opportunity.c.id == oid)).mappings().first()
    if row is None:
        raise OpportunityError("No such opportunity.")
    return dict(row)


def change(engine: Engine, oid: str, data: dict[str, Any], actor: str) -> None:
    """Change some fields; ``archived`` false restores an archived opportunity."""
    data = dict(data)
    restore = data.pop("archived", None)
    _row(engine, oid)
    values = clean(data, partial=True)
    if restore is False:
        values["archived_at"] = None
    with engine.begin() as conn:
        conn.execute(update(opportunity).where(opportunity.c.id == oid)
                     .values(**values, updated_by=actor, updated_at=utc_now()))  # fmt: skip


def archive(engine: Engine, oid: str, actor: str) -> None:
    _row(engine, oid)
    now = utc_now()
    with engine.begin() as conn:
        conn.execute(update(opportunity).where(opportunity.c.id == oid)
                     .values(archived_at=now, updated_by=actor, updated_at=now))  # fmt: skip


# --- import ----------------------------------------------------------------------------------


def csv_rows(text: str) -> list[dict[str, Any]]:
    """Rows from CSV text with a header."""
    return list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))


def read_rows(path: Path) -> list[dict[str, Any]]:
    """Rows from a CSV file (with a header) or a JSON list of objects."""
    text = path.read_text("utf-8-sig")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("opportunities", [])
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise OpportunityError("A JSON file holds a list of opportunities (objects).")
        return data
    return csv_rows(text)


def _key(values: dict[str, Any]) -> str:
    return (values.get("external_id") or values.get("number") or values["title"])[:200]


def import_rows(
    engine: Engine,
    rows: Iterable[dict[str, Any]],
    actor: str,
    *,
    source: str | None = None,
    complete: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Add or update opportunities by (source, external ID). Nothing is written when any row
    has a problem, or with ``dry_run``."""
    rows = list(rows)
    if len(rows) > MAX_IMPORT:
        raise OpportunityError(f"Import at most {MAX_IMPORT:,} opportunities at a time.")
    problems: list[str] = []
    cleaned: list[dict[str, Any]] = []
    for n, raw in enumerate(rows, start=1):
        data = {k: v for k, v in raw.items() if k in FIELDS and v not in (None, "")}
        if source:
            data["source"] = source
        try:
            values = clean(data)
        except OpportunityError as exc:
            problems.append(f"row {n}: {exc}")
            continue
        values["external_id"] = _key(values)
        cleaned.append(values)
    keys = [(v["source"], v["external_id"]) for v in cleaned]
    dupes = sorted({f"{s}: {k}" for s, k in keys if keys.count((s, k)) > 1})
    problems += [f"listed twice: {d}" for d in dupes]
    with engine.connect() as conn:
        existing = {
            (r["source"], r["external_id"]): dict(r)
            for r in conn.execute(select(opportunity).where(
                opportunity.c.source.in_(sorted({s for s, _ in keys}) or [""]))).mappings()
        }  # fmt: skip
    result: dict[str, Any] = {"created": 0, "updated": 0, "unchanged": 0, "archived": 0,
                              "problems": problems, "dry_run": dry_run}  # fmt: skip
    if problems:
        return result
    now = utc_now()
    plan: list[tuple[str, dict[str, Any], dict[str, Any] | None]] = []
    for values in cleaned:
        old = existing.get((values["source"], values["external_id"]))
        if old is None:
            result["created"] += 1
            plan.append(("create", values, None))
            continue
        fresh = {"kind": "foundation", "limited_submission": False, "applicant_types": [],
                 **dict.fromkeys((*TEXT_LIMITS, *DATES, *MONEY)), **values}  # fmt: skip
        changed = {k: v for k, v in fresh.items() if old.get(k) != v}
        if old["archived_at"] is not None:
            changed["archived_at"] = None
        if changed:
            result["updated"] += 1
            plan.append(("update", changed, old))
        else:
            result["unchanged"] += 1
    listed = set(keys)
    stale = []
    if complete:
        for (src, key), old in existing.items():
            if src in {s for s, _ in keys} and (src, key) not in listed and not old["archived_at"]:
                stale.append(old["id"])
        result["archived"] = len(stale)
    if dry_run:
        return result
    with engine.begin() as conn:
        for action, values, old in plan:
            if action == "create":
                row = {"kind": "foundation", "limited_submission": False, "applicant_types": [],
                       **values}  # fmt: skip
                conn.execute(insert(opportunity).values(
                    id=new_id("opl"), **row, created_by=actor, created_at=now, updated_by=actor,
                    updated_at=now))  # fmt: skip
            else:
                assert old is not None
                conn.execute(update(opportunity).where(opportunity.c.id == old["id"])
                             .values(**values, updated_by=actor, updated_at=now))  # fmt: skip
        if stale:
            conn.execute(update(opportunity).where(opportunity.c.id.in_(stale))
                         .values(archived_at=now, updated_by=actor, updated_at=now))  # fmt: skip
    return result


__all__ = [
    "FIELDS",
    "OpportunityError",
    "archive",
    "change",
    "clean",
    "create",
    "csv_rows",
    "import_rows",
    "interests",
    "read_rows",
    "set_interest",
]


# --- watching and dismissing, for oneself -------------------------------------------------------


def set_interest(engine: Engine, owner: str, ref: str, state: str | None) -> None:
    """Watch an opportunity, dismiss it from one's own lists, or (None) neither."""
    from sqlalchemy import delete

    from tessera.ledger.tables import INTEREST_STATES, opportunity_interest

    if state is not None and state not in INTEREST_STATES:
        raise OpportunityError(f"state is one of {', '.join(INTEREST_STATES)}, or null.")
    where = (opportunity_interest.c.owner == owner) & (opportunity_interest.c.opportunity == ref)
    with engine.begin() as conn:
        conn.execute(delete(opportunity_interest).where(where))
        if state is not None:
            conn.execute(insert(opportunity_interest).values(
                owner=owner, opportunity=ref, state=state, updated_at=utc_now()))  # fmt: skip


def interests(engine: Engine, owner: str) -> dict[str, str]:
    """{opportunity ref: "watching" or "dismissed"} for one owner."""
    from tessera.ledger.tables import opportunity_interest

    with engine.connect() as conn:
        rows = conn.execute(
            select(opportunity_interest.c.opportunity, opportunity_interest.c.state).where(
                opportunity_interest.c.owner == owner)
        ).all()  # fmt: skip
    return dict(rows)
