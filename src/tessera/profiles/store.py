"""Profile entries: who may keep them, the changes, and their history (PRF-02, PRF-06).

Changes take effect at once; each one is a profile event with the entry before and after, the
actor, and the capacity they acted in. Entries made by hand have the source ``manual``. An import
gives its own source name and its key for each entry (``source_id``), and ``upsert`` finds the
entry by that key, so importing twice never adds a second copy and never touches entries made by
hand.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Connection, Engine

from tessera.core.clock import utc_now
from tessera.core.ids import new_id
from tessera.evidence.store import survivor_of
from tessera.ledger.tables import assertion, person, profile_entry, profile_event
from tessera.profiles.sections import SECTIONS, ProfileError, clean

KEEPERS = ("self", "curator", "admin")  # the capacities in which someone keeps a profile


def capacity(principal: Any, engine: Engine, person_id: str) -> str | None:
    """How this principal may keep the person's profile ("self", "admin", "curator"), or None
    when they may only read it."""
    if principal.person_id:
        with engine.connect() as conn:
            if survivor_of(conn, principal.person_id) == person_id:
                return "self"
    if principal.is_admin:
        return "admin"
    if principal.curates_any and principal.can_curate(engine, person_id):
        return "curator"
    return None


def _member(conn: Connection, person_id: str) -> dict[str, Any]:
    row = conn.execute(select(person).where(person.c.id == person_id)).mappings().first()
    if row is None:
        raise ProfileError("No such person.")
    if row["status"] not in ("active", "former"):
        raise ProfileError("Only people in the directory have profiles.")
    return dict(row)


def _event(
    conn: Connection,
    person_id: str,
    entry_id: str | None,
    section: str,
    action: str,
    actor: str,
    capacity: str,
    before: Any = None,
    after: Any = None,
) -> None:
    conn.execute(
        insert(profile_event).values(
            person_id=person_id,
            entry_id=entry_id,
            section=section,
            action=action,
            actor=actor,
            capacity=capacity,
            before=before,
            after=after,
            at=utc_now(),
        )
    )


def _view(row: Any) -> dict[str, Any]:
    return {
        k: row[k]
        for k in (
            "id",
            "section",
            "fields",
            "position",
            "source",
            "source_id",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
        )
    }


def confirmed_papers(conn: Connection, person_id: str) -> set[str]:
    return {
        r.removeprefix("pub:")
        for r in conn.execute(
            select(assertion.c.object_id).where(
                assertion.c.subject_id == person_id,
                assertion.c.predicate == "authored",
                assertion.c.status == "accepted",
            )
        ).scalars()
    }


def entries(conn: Connection, person_id: str) -> dict[str, list[dict[str, Any]]]:
    """Every section's entries, in their order (sections without entries are absent)."""
    out: dict[str, list[dict[str, Any]]] = {}
    rows = conn.execute(
        select(profile_entry)
        .where(profile_entry.c.person_id == person_id)
        .order_by(profile_entry.c.section, profile_entry.c.position)
    ).mappings()
    for row in rows:
        out.setdefault(row["section"], []).append(_view(row))
    return out


def get(conn: Connection, person_id: str, entry_id: str) -> dict[str, Any]:
    row = (
        conn.execute(
            select(profile_entry).where(
                profile_entry.c.id == entry_id, profile_entry.c.person_id == person_id
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ProfileError("No such entry on this profile.")
    return _view(row)


def _check(
    conn: Connection,
    person_id: str,
    section: str,
    fields: dict[str, Any],
    entry_id: str | None = None,
) -> None:
    spec = SECTIONS[section]
    others = conn.execute(
        select(profile_entry.c.id, profile_entry.c.fields).where(
            profile_entry.c.person_id == person_id, profile_entry.c.section == section
        )
    ).all()
    others = [o for o in others if o.id != entry_id]
    if spec.single and others:
        raise ProfileError(f"A profile has one {spec.label.lower()} entry; change it instead.")
    if len(others) >= spec.most:
        raise ProfileError(f"A profile has at most {spec.most} entries in {spec.label}.")
    if section == "featured":
        if fields["pmid"] not in confirmed_papers(conn, person_id):
            raise ProfileError("A featured publication must be one of their confirmed papers.")
        if any(o.fields.get("pmid") == fields["pmid"] for o in others):
            raise ProfileError("That paper is already featured.")


def add(
    engine: Engine,
    person_id: str,
    section: str,
    data: Any,
    *,
    actor: str,
    capacity: str,
    source: str = "manual",
    source_id: str | None = None,
) -> dict[str, Any]:
    fields = clean(section, data)
    with engine.begin() as conn:
        _member(conn, person_id)
        _check(conn, person_id, section, fields)
        ends = conn.execute(
            select(func.min(profile_entry.c.position), func.max(profile_entry.c.position)).where(
                profile_entry.c.person_id == person_id, profile_entry.c.section == section
            )
        ).one()
        if ends[0] is None:
            position = 0
        else:
            position = ends[0] - 1 if SECTIONS[section].newest_first else ends[1] + 1
        now = utc_now()
        entry_id = new_id("pfe")
        conn.execute(
            insert(profile_entry).values(
                id=entry_id,
                person_id=person_id,
                section=section,
                fields=fields,
                position=position,
                source=source,
                source_id=source_id,
                created_at=now,
                created_by=actor,
                updated_at=now,
                updated_by=actor,
            )
        )
        _event(conn, person_id, entry_id, section, "added", actor, capacity, after=fields)
        return get(conn, person_id, entry_id)


def change(
    engine: Engine, person_id: str, entry_id: str, data: Any, *, actor: str, capacity: str
) -> dict[str, Any]:
    with engine.begin() as conn:
        _member(conn, person_id)
        before = get(conn, person_id, entry_id)
        fields = clean(before["section"], data)
        if fields == before["fields"]:
            return before
        _check(conn, person_id, before["section"], fields, entry_id)
        conn.execute(
            update(profile_entry)
            .where(profile_entry.c.id == entry_id)
            .values(fields=fields, updated_at=utc_now(), updated_by=actor)
        )
        _event(
            conn,
            person_id,
            entry_id,
            before["section"],
            "changed",
            actor,
            capacity,
            before=before["fields"],
            after=fields,
        )
        return get(conn, person_id, entry_id)


def remove(engine: Engine, person_id: str, entry_id: str, *, actor: str, capacity: str) -> None:
    with engine.begin() as conn:
        before = get(conn, person_id, entry_id)
        conn.execute(delete(profile_entry).where(profile_entry.c.id == entry_id))
        _event(
            conn,
            person_id,
            entry_id,
            before["section"],
            "removed",
            actor,
            capacity,
            before=before["fields"],
        )


def reorder(
    engine: Engine, person_id: str, section: str, ids: list[str], *, actor: str, capacity: str
) -> list[dict[str, Any]]:
    """Put a section's entries in this order; the list must name each of them once."""
    if section not in SECTIONS:
        raise ProfileError(f"There is no profile section {section!r}.")
    with engine.begin() as conn:
        _member(conn, person_id)
        current = [
            r.id
            for r in conn.execute(
                select(profile_entry.c.id)
                .where(profile_entry.c.person_id == person_id, profile_entry.c.section == section)
                .order_by(profile_entry.c.position)
            )
        ]
        if sorted(current) != sorted(ids) or len(set(ids)) != len(ids):
            raise ProfileError("Give every entry of the section once, in the order wanted.")
        if current == ids:
            return entries(conn, person_id).get(section, [])
        for n, entry_id in enumerate(ids):
            conn.execute(
                update(profile_entry).where(profile_entry.c.id == entry_id).values(position=n)
            )
        _event(
            conn, person_id, None, section, "reordered", actor, capacity, before=current, after=ids
        )
        return entries(conn, person_id).get(section, [])


def upsert(
    engine: Engine,
    person_id: str,
    section: str,
    data: Any,
    *,
    source: str,
    source_id: str,
    actor: str,
) -> dict[str, Any]:
    """For an import: add the entry, or change the one this source made before with this key.
    Entries made by hand are never matched, so an import never overwrites them."""
    if source == "manual" or not source_id:
        raise ProfileError("An import names its source and its key for the entry.")
    with engine.connect() as conn:
        row = conn.execute(
            select(profile_entry.c.id).where(
                profile_entry.c.person_id == person_id,
                profile_entry.c.source == source,
                profile_entry.c.source_id == source_id,
            )
        ).first()
    if row is None:
        return add(
            engine,
            person_id,
            section,
            data,
            actor=actor,
            capacity="import",
            source=source,
            source_id=source_id,
        )
    return change(engine, person_id, row.id, data, actor=actor, capacity="import")


def history(conn: Connection, person_id: str, limit: int = 200) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            select(profile_event)
            .where(profile_event.c.person_id == person_id)
            .order_by(profile_event.c.id.desc())
            .limit(limit)
        ).mappings()
    ]


__all__ = [
    "KEEPERS",
    "add",
    "capacity",
    "change",
    "confirmed_papers",
    "entries",
    "get",
    "history",
    "remove",
    "reorder",
    "upsert",
]
