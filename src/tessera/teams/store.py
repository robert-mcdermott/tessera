"""Teams (INT-10): kept in the ledger, visible to their owner, their members, the people they
are shared with, and admins (and to the operator without accounts)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from tessera.core.clock import utc_now
from tessera.core.errors import TesseraError
from tessera.core.ids import new_id
from tessera.ledger.tables import TEAM_ROLES, person, team, team_member

MAX_MEMBERS = 60
MAX_CONCEPTS = 30
ROLE_LABELS = {
    "lead": "Lead",
    "co_investigator": "Co-investigator",
    "collaborator": "Collaborator",
    "core": "Core or resource",
    "other": "Other",
}


class TeamError(TesseraError):
    """A team that cannot be made or changed as asked."""


class TeamNotFound(TeamError):
    """No such team, or not one this person may see."""


class TeamForbidden(TeamError):
    """Something only the team's owner, or an admin, may do."""


def owner_of(principal: Any) -> str:
    return principal.account_id or "operator"


def _clean_concepts(concepts: Any) -> list[dict[str, Any]]:
    if not isinstance(concepts, list):
        raise TeamError("concepts is a list of topics.")
    out: list[dict[str, Any]] = []
    for c in concepts[:MAX_CONCEPTS]:
        ui = str((c or {}).get("descriptor_ui") or "").removeprefix("msh:").strip()
        if not ui or len(ui) > 40:
            raise TeamError("Each topic needs a MeSH descriptor ID.")
        if any(o["descriptor_ui"] == ui for o in out):
            continue
        weight = c.get("weight")
        weight = float(weight) if isinstance(weight, int | float) else 1.0
        name = str(c.get("descriptor") or ui)[:200]
        out.append({"descriptor_ui": ui, "descriptor": name, "weight": weight})
    return out


def _text(value: Any, limit: int, name: str, *, required: bool = False) -> str | None:
    text = str(value).strip() if value is not None else ""
    if required and not text:
        raise TeamError(f"A team needs a {name}.")
    if len(text) > limit:
        raise TeamError(f"{name.capitalize()} has at most {limit:,} characters.")
    return text or None


def create(engine: Engine, principal: Any, fields: dict[str, Any]) -> str:
    now = utc_now()
    tid = new_id("tem")
    row = {
        "id": tid,
        "name": _text(fields.get("name"), 200, "name", required=True),
        "purpose": _text(fields.get("purpose"), 5000, "purpose"),
        "opportunity": _text(fields.get("opportunity"), 80, "opportunity"),
        "concepts": _clean_concepts(fields.get("concepts") or []),
        "notes": _text(fields.get("notes"), 10000, "notes"),
        "shared_with": [],
        "owner": owner_of(principal),
        "owner_name": principal.name,
        "created_at": now,
        "updated_at": now,
    }
    with engine.begin() as conn:
        conn.execute(insert(team).values(**row))
    return tid


def get(engine: Engine, team_id: str) -> dict[str, Any]:
    with engine.connect() as conn:
        row = conn.execute(select(team).where(team.c.id == team_id)).mappings().first()
        if row is None:
            raise TeamNotFound("No such team.")
        members = [dict(r) for r in conn.execute(
            select(team_member, person.c.display_name.label("name"))
            .join(person, person.c.id == team_member.c.person_id)
            .where(team_member.c.team_id == team_id)
            .order_by(team_member.c.added_at, person.c.display_name)
        ).mappings()]  # fmt: skip
    return {**dict(row), "members": members}


def visible(found: dict[str, Any], principal: Any) -> bool:
    if principal.single_operator or principal.is_admin:
        return True
    if found["owner"] == owner_of(principal):
        return True
    me = principal.person_id
    return bool(me) and (me in (found["shared_with"] or [])
                         or any(m["person_id"] == me for m in found["members"]))  # fmt: skip


def owns(found: dict[str, Any], principal: Any) -> bool:
    return bool(principal.single_operator or principal.is_admin
                or found["owner"] == owner_of(principal))  # fmt: skip


def get_visible(engine: Engine, team_id: str, principal: Any) -> dict[str, Any]:
    found = get(engine, team_id)
    if not visible(found, principal):
        raise TeamNotFound("No such team.")
    return found


def list_for(engine: Engine, principal: Any) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        ids = [r[0] for r in conn.execute(select(team.c.id).order_by(team.c.updated_at.desc()))]
    out = []
    for tid in ids:
        found = get(engine, tid)
        if visible(found, principal):
            out.append(found)
    return out


def change(engine: Engine, team_id: str, fields: dict[str, Any], principal: Any) -> None:
    found = get_visible(engine, team_id, principal)
    values: dict[str, Any] = {}
    for name, limit in (("purpose", 5000), ("notes", 10000), ("opportunity", 80)):
        if name in fields:
            values[name] = _text(fields[name], limit, name)
    if "name" in fields:
        values["name"] = _text(fields["name"], 200, "name", required=True)
    if "concepts" in fields:
        values["concepts"] = _clean_concepts(fields["concepts"])
    if "shared_with" in fields:
        if not owns(found, principal):
            raise TeamForbidden("Only the team's owner, or an admin, shares it.")
        people = fields["shared_with"]
        if not isinstance(people, list) or not all(isinstance(p, str) for p in people):
            raise TeamError("shared_with is a list of person IDs.")
        with engine.connect() as conn:
            known = {r[0] for r in conn.execute(select(person.c.id).where(person.c.id.in_(people)))}
        unknown = [p for p in people if p not in known]
        if unknown:
            raise TeamError(f"No such person: {', '.join(unknown[:3])}.")
        values["shared_with"] = sorted(set(people))
    unknown = set(fields) - {"name", "purpose", "notes", "opportunity", "concepts", "shared_with"}
    if unknown:
        raise TeamError(f"Unknown fields: {', '.join(sorted(unknown))}.")
    with engine.begin() as conn:
        conn.execute(update(team).where(team.c.id == team_id)
                     .values(**values, updated_at=utc_now()))  # fmt: skip


def remove(engine: Engine, team_id: str, principal: Any) -> None:
    found = get_visible(engine, team_id, principal)
    if not owns(found, principal):
        raise TeamForbidden("Only the team's owner, or an admin, deletes it.")
    with engine.begin() as conn:
        conn.execute(delete(team_member).where(team_member.c.team_id == team_id))
        conn.execute(delete(team).where(team.c.id == team_id))


def set_member(
    engine: Engine, team_id: str, person_id: str, principal: Any,
    role: str = "collaborator", note: str | None = None,
) -> None:  # fmt: skip
    """Add someone, or change their role or note (the owner, or an admin: a member is someone
    the team is visible to, so adding one shares it)."""
    found = get_visible(engine, team_id, principal)
    if not owns(found, principal):
        raise TeamForbidden("Only the team's owner, or an admin, changes who is on it.")
    if role not in TEAM_ROLES:
        raise TeamError(f"role is one of {', '.join(TEAM_ROLES)}.")
    note = _text(note, 500, "note")
    with engine.begin() as conn:
        if conn.execute(select(person.c.id).where(person.c.id == person_id)).first() is None:
            raise TeamError("No such person.")
        where = (team_member.c.team_id == team_id) & (team_member.c.person_id == person_id)
        if conn.execute(select(team_member.c.person_id).where(where)).first():
            conn.execute(update(team_member).where(where).values(role=role, note=note))
        else:
            if len(found["members"]) >= MAX_MEMBERS:
                raise TeamError(f"A team has at most {MAX_MEMBERS} members.")
            conn.execute(insert(team_member).values(
                team_id=team_id, person_id=person_id, role=role, note=note,
                added_by=principal.name, added_at=utc_now()))  # fmt: skip
        conn.execute(update(team).where(team.c.id == team_id).values(updated_at=utc_now()))


def remove_member(engine: Engine, team_id: str, person_id: str, principal: Any) -> None:
    if not owns(get_visible(engine, team_id, principal), principal):
        raise TeamForbidden("Only the team's owner, or an admin, changes who is on it.")
    with engine.begin() as conn:
        conn.execute(delete(team_member).where(team_member.c.team_id == team_id,
                                                team_member.c.person_id == person_id))  # fmt: skip
        conn.execute(update(team).where(team.c.id == team_id).values(updated_at=utc_now()))


__all__ = [
    "ROLE_LABELS",
    "TeamError",
    "TeamForbidden",
    "TeamNotFound",
    "change",
    "create",
    "get",
    "get_visible",
    "list_for",
    "owner_of",
    "owns",
    "remove",
    "remove_member",
    "set_member",
    "visible",
]
