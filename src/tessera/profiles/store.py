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
from tessera.ledger.tables import (
    account,
    assertion,
    person,
    profile_delegate,
    profile_entry,
    profile_event,
    profile_public,
)
from tessera.profiles.sections import SECTIONS, ProfileError, clean

# The capacities in which someone keeps a profile; those who may also name someone to act for
# the person (a delegate keeps the profile but does not choose who else does).
KEEPERS = ("self", "curator", "admin", "delegate")
NAMERS = ("self", "curator", "admin")


def capacity(principal: Any, engine: Engine, person_id: str) -> str | None:
    """How this principal may keep the person's profile ("self", "admin", "curator", or
    "delegate"), or None when they may only read it."""
    with engine.connect() as conn:
        if principal.person_id and survivor_of(conn, principal.person_id) == person_id:
            return "self"
        if principal.is_admin:
            return "admin"
        if principal.curates_any and principal.can_curate(engine, person_id):
            return "curator"
        if (
            principal.account_id
            and conn.execute(
                select(profile_delegate.c.account_id).where(
                    profile_delegate.c.person_id == person_id,
                    profile_delegate.c.account_id == principal.account_id,
                )
            ).first()
        ):
            return "delegate"
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
            "hidden",
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


# The sections a public page can show, in its order: the ones a person keeps, and those Tessera
# builds from records. Each can be left off; everything is always shown inside.
BUILT = {
    "summary": "Summary",  # the approved plain-language summary, with the assistant on
    "groups": "Groups",
    "publications": "Publications",
    "grants": "Grants",
    "studies": "Studies",
    "topics": "Topics",
}
PUBLIC_SECTIONS: tuple[str, ...] = (
    "summary",
    "overview",
    "interests",
    "groups",
    "position",
    "education",
    "honor",
    "membership",
    "teaching",
    "talk",
    "link",
    "featured",
    "publications",
    "grants",
    "studies",
    "topics",
)


def public_label(section: str) -> str:
    return SECTIONS[section].label if section in SECTIONS else BUILT[section]


def hidden_sections(conn: Connection, person_id: str) -> list[str]:
    row = conn.execute(
        select(profile_public.c.hidden_sections).where(profile_public.c.person_id == person_id)
    ).first()
    return [s for s in (row.hidden_sections if row else []) if s in PUBLIC_SECTIONS]


def set_public(
    engine: Engine,
    person_id: str,
    *,
    actor: str,
    capacity: str,
    public: bool | None = None,
    hidden: list[str] | None = None,
) -> dict[str, Any]:
    """Turn the public page on or off, and choose which sections it leaves off."""
    if hidden is not None:
        unknown = set(hidden) - set(PUBLIC_SECTIONS)
        if unknown:
            raise ProfileError(f"There is no public section {sorted(unknown)[0]!r}.")
        hidden = [s for s in PUBLIC_SECTIONS if s in set(hidden)]
    with engine.begin() as conn:
        row = _member(conn, person_id)
        if public is not None and bool(row["public"]) != public:
            conn.execute(update(person).where(person.c.id == person_id).values(public=public))
            _event(
                conn,
                person_id,
                None,
                "public",
                "published" if public else "unpublished",
                actor,
                capacity,
            )
        before = hidden_sections(conn, person_id)
        if hidden is not None and hidden != before:
            now = utc_now()
            if conn.execute(
                select(profile_public.c.person_id).where(profile_public.c.person_id == person_id)
            ).first():
                conn.execute(
                    update(profile_public)
                    .where(profile_public.c.person_id == person_id)
                    .values(hidden_sections=hidden, updated_at=now, updated_by=actor)
                )
            else:
                conn.execute(
                    insert(profile_public).values(
                        person_id=person_id,
                        hidden_sections=hidden,
                        updated_at=now,
                        updated_by=actor,
                    )
                )
            _event(
                conn,
                person_id,
                None,
                "public",
                "sections",
                actor,
                capacity,
                before=before,
                after=hidden,
            )
        current = conn.execute(select(person.c.public).where(person.c.id == person_id)).scalar()
        return {"public": bool(current), "hidden_sections": hidden_sections(conn, person_id)}


def set_hidden(
    engine: Engine, person_id: str, entry_id: str, hidden: bool, *, actor: str, capacity: str
) -> dict[str, Any]:
    """Leave one entry off the public page, or put it back."""
    with engine.begin() as conn:
        before = get(conn, person_id, entry_id)
        if before["hidden"] != hidden:
            conn.execute(
                update(profile_entry).where(profile_entry.c.id == entry_id).values(hidden=hidden)
            )
            _event(
                conn,
                person_id,
                entry_id,
                before["section"],
                "hidden" if hidden else "shown",
                actor,
                capacity,
                before=before["fields"],
            )
        return get(conn, person_id, entry_id)


def public_profile(conn: Connection, person_id: str) -> dict[str, Any]:
    """What the public page may show from the profile: the sections not left off, and in them
    the entries not hidden (featured papers only while still confirmed)."""
    hidden = set(hidden_sections(conn, person_id))
    found = entries(conn, person_id)
    confirmed = confirmed_papers(conn, person_id) if found.get("featured") else set()
    shown: dict[str, list[dict[str, Any]]] = {}
    for section, rows in found.items():
        if section in hidden:
            continue
        rows = [r for r in rows if not r["hidden"]]
        if section == "featured":
            rows = [r for r in rows if r["fields"]["pmid"] in confirmed]
        if rows:
            shown[section] = rows
    return {"hidden_sections": sorted(hidden), "sections": shown}


def delegates(conn: Connection, person_id: str) -> list[dict[str, Any]]:
    """The accounts that keep this person's profile for them."""
    return [
        dict(r)
        for r in conn.execute(
            select(
                profile_delegate.c.account_id,
                account.c.display_name.label("name"),
                account.c.username,
                account.c.status,
                profile_delegate.c.granted_by,
                profile_delegate.c.granted_at,
            )
            .join(account, account.c.id == profile_delegate.c.account_id)
            .where(profile_delegate.c.person_id == person_id)
            .order_by(account.c.display_name)
        ).mappings()
    ]


def name_delegate(
    engine: Engine, person_id: str, username: str, *, actor: str, capacity: str
) -> dict[str, Any]:
    """Let an account keep this person's profile for them; recorded in the history."""
    if capacity not in NAMERS:
        raise ProfileError(
            "Only the person, a curator for them, or an admin names who acts for them."
        )
    key = (username or "").strip().lower()
    with engine.begin() as conn:
        _member(conn, person_id)
        row = conn.execute(select(account).where(account.c.username == key)).mappings().first()
        if row is None or row["status"] != "active":
            raise ProfileError(f"No active account is called {username!r}.")
        if row["person_id"] and survivor_of(conn, row["person_id"]) == person_id:
            raise ProfileError("That is the person's own account; it keeps the profile already.")
        if conn.execute(
            select(profile_delegate.c.account_id).where(
                profile_delegate.c.person_id == person_id,
                profile_delegate.c.account_id == row["id"],
            )
        ).first():
            raise ProfileError(f"{row['display_name']} already keeps this profile.")
        conn.execute(
            insert(profile_delegate).values(
                person_id=person_id, account_id=row["id"], granted_by=actor, granted_at=utc_now()
            )
        )
        _event(
            conn,
            person_id,
            None,
            "delegates",
            "added",
            actor,
            capacity,
            after={"name": row["display_name"], "username": row["username"]},
        )
        return next(d for d in delegates(conn, person_id) if d["account_id"] == row["id"])


def remove_delegate(
    engine: Engine, person_id: str, account_id: str, *, actor: str, capacity: str
) -> None:
    if capacity not in NAMERS:
        raise ProfileError(
            "Only the person, a curator for them, or an admin names who acts for them."
        )
    with engine.begin() as conn:
        found = next((d for d in delegates(conn, person_id) if d["account_id"] == account_id), None)
        if found is None:
            raise ProfileError("No such account keeps this profile.")
        conn.execute(
            delete(profile_delegate).where(
                profile_delegate.c.person_id == person_id,
                profile_delegate.c.account_id == account_id,
            )
        )
        _event(
            conn,
            person_id,
            None,
            "delegates",
            "removed",
            actor,
            capacity,
            before={"name": found["name"], "username": found["username"]},
        )


def kept_by(conn: Connection, principal: Any) -> list[dict[str, Any]]:
    """The profiles this account keeps: its own person's, and those it acts for."""
    out: list[dict[str, Any]] = []
    if principal.person_id:
        own = survivor_of(conn, principal.person_id)
        row = conn.execute(
            select(person.c.id, person.c.display_name).where(person.c.id == own)
        ).first()
        if row:
            out.append({"person_id": row.id, "name": row.display_name, "capacity": "self"})
    if principal.account_id:
        for row in conn.execute(
            select(person.c.id, person.c.display_name)
            .join(profile_delegate, profile_delegate.c.person_id == person.c.id)
            .where(profile_delegate.c.account_id == principal.account_id)
            .order_by(person.c.display_name)
        ):
            out.append({"person_id": row.id, "name": row.display_name, "capacity": "delegate"})
    return out


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
    "NAMERS",
    "PUBLIC_SECTIONS",
    "add",
    "capacity",
    "change",
    "confirmed_papers",
    "delegates",
    "entries",
    "get",
    "hidden_sections",
    "history",
    "kept_by",
    "name_delegate",
    "public_label",
    "public_profile",
    "remove",
    "remove_delegate",
    "reorder",
    "set_hidden",
    "set_public",
    "upsert",
]
