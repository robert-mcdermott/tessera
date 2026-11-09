"""Ledger access for people, units, and assertions.

The rules from ADR-0006 are enforced here, in one place:

* automation never changes the status of a locked (human-decided) assertion;
* automation never deletes an assertion; links no longer produced are marked unreported;
* every status change by a person is an append-only curation event, and undo is an event;
* decisions about people (promote, dismiss) follow the same rules in person_event.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import and_, bindparam, delete, insert, select, update
from sqlalchemy.engine import Connection, Engine

from tessera.core.clock import utc_now
from tessera.core.errors import TesseraError
from tessera.core.ids import new_id
from tessera.evidence.names import Name, make_name
from tessera.ledger.tables import (
    assertion,
    curation_event,
    person,
    person_event,
    person_identifier,
    person_name,
)

# --- people ---------------------------------------------------------------------------------


@dataclass
class PersonRecord:
    id: str
    display_name: str
    status: str
    names: list[Name] = field(default_factory=list)
    identifiers: dict[str, set[str]] = field(default_factory=dict)  # scheme -> values
    rejected: dict[str, set[str]] = field(default_factory=dict)  # marked as not this person's
    suggested: dict[str, set[str]] = field(default_factory=dict)  # proposed, not yet decided

    def ids(self, scheme: str) -> set[str]:
        return self.identifiers.get(scheme, set())

    def rejected_ids(self, scheme: str) -> set[str]:
        return self.rejected.get(scheme, set())

    def suggested_ids(self, scheme: str) -> set[str]:
        return self.suggested.get(scheme, set())


def find_by_identifier(conn: Connection, scheme: str, value: str) -> str | None:
    """The person holding this identifier (accepted or suggested; never a rejected one).

    A record merged into another answers with the record that holds the person now.
    """
    found = conn.execute(
        select(person_identifier.c.person_id)
        .where(
            and_(
                person_identifier.c.scheme == scheme,
                person_identifier.c.value == value,
                person_identifier.c.status != "rejected",
            )
        )
        .order_by(person_identifier.c.status)  # "accepted" sorts before "suggested"
    ).scalar()
    return survivor_of(conn, found) if found else None


def survivor_of(conn: Connection, person_id: str) -> str:
    """Follow merges to the record that holds this person now."""
    seen = {person_id}
    while True:
        target = conn.execute(
            select(person.c.merged_into).where(
                person.c.id == person_id, person.c.status == "merged"
            )
        ).scalar()
        if not target or target in seen:
            return person_id
        seen.add(target)
        person_id = target


def create_person(
    conn: Connection, name: Name, *, status: str, source: str, display_name: str | None = None
) -> str:
    now = utc_now()
    person_id = new_id("per")
    conn.execute(
        insert(person).values(
            id=person_id,
            display_name=display_name or name.display,
            family_name=name.family,
            given_name=name.given or None,
            status=status,
            created_at=now,
            updated_at=now,
        )
    )
    add_name(conn, person_id, name, kind="primary", source=source)
    return person_id


def add_name(conn: Connection, person_id: str, name: Name, *, kind: str, source: str) -> bool:
    exists = conn.execute(
        select(person_name.c.id).where(
            and_(
                person_name.c.person_id == person_id,
                person_name.c.family == name.family,
                person_name.c.given == name.given,
            )
        )
    ).first()
    if exists:
        return False
    conn.execute(
        insert(person_name).values(
            person_id=person_id,
            family=name.family,
            given=name.given,
            initials=name.initials,
            kind=kind,
            source=source,
            created_at=utc_now(),
        )
    )
    return True


def add_identifier(
    conn: Connection,
    person_id: str,
    scheme: str,
    value: str,
    *,
    source: str,
    status: str = "accepted",
) -> bool:
    exists = conn.execute(
        select(person_identifier.c.id).where(
            and_(
                person_identifier.c.person_id == person_id,
                person_identifier.c.scheme == scheme,
                person_identifier.c.value == value,
            )
        )
    ).first()
    if exists:
        return False
    now = utc_now()
    conn.execute(
        insert(person_identifier).values(
            person_id=person_id,
            scheme=scheme,
            value=value,
            status=status,
            source=source,
            created_at=now,
            updated_at=now,
        )
    )
    return True


def set_status(conn: Connection, person_id: str, status: str) -> None:
    conn.execute(
        update(person).where(person.c.id == person_id).values(status=status, updated_at=utc_now())
    )


# A curator's decisions about people. Promote makes a candidate (or a dismissed person) a
# member of the directory; dismiss says a candidate is not one. Automation never reverses
# either: it only ever promotes candidates, and discovery skips dismissed people.
PERSON_DECISIONS = {
    "promote": ({"candidate", "dismissed"}, "active"),
    "dismiss": ({"candidate"}, "dismissed"),
}


def decide_person(
    engine: Engine, person_id: str, action: str, actor: str, note: str | None = None
) -> dict[str, Any]:
    """Promote, dismiss, or undo the latest decision about a person. Logged in person_event."""
    with engine.begin() as conn:
        current = conn.execute(select(person.c.status).where(person.c.id == person_id)).scalar()
        if current is None:
            raise TesseraError(f"No person {person_id}")
        if action == "undo":
            events = conn.execute(
                select(person_event)
                .where(
                    person_event.c.person_id == person_id,
                    person_event.c.action != "identifier",
                )
                .order_by(person_event.c.id)
            ).mappings()
            stack: list[Any] = []
            for event in events:
                if event["action"] == "undo":
                    if stack:
                        stack.pop()
                else:
                    stack.append(event)
            if not stack:
                raise TesseraError("Nothing to undo for this person.")
            new_status = stack[-1]["from_status"] or "candidate"
        elif action in PERSON_DECISIONS:
            allowed, new_status = PERSON_DECISIONS[action]
            if current not in allowed:
                raise TesseraError(f"Cannot {action} a person who is {current}.")
        else:
            raise TesseraError(f"Unknown action {action!r}; use promote, dismiss, or undo")
        now = utc_now()
        conn.execute(
            update(person).where(person.c.id == person_id).values(status=new_status, updated_at=now)
        )
        conn.execute(
            insert(person_event).values(
                person_id=person_id,
                actor=actor,
                action=action,
                from_status=current,
                to_status=new_status,
                note=note,
                at=now,
            )
        )
    return {"id": person_id, "status": new_status, "previous": current}


# A curator's decisions about a person's identifiers. Each maps the identifier's current
# status (None when the person does not have it) to the new one (None removes it).
IDENTIFIER_ACTIONS = {
    "add": ({None, "suggested", "rejected"}, "accepted"),
    "accept": ({"suggested", "rejected"}, "accepted"),
    "reject": ({None, "suggested", "accepted"}, "rejected"),
    "remove": ({"suggested", "accepted", "rejected"}, None),
}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class InvalidIdentifier(TesseraError):
    """An identifier that is malformed, or a request that names an unknown type or action."""


def normalize_identifier(scheme: str, value: str, *, strict: bool = True) -> str:
    """The canonical form of an identifier, or an InvalidIdentifier explaining what is wrong.

    ``strict`` also checks an ORCID iD's check digit (for new identifiers; decisions about
    ones already recorded only need the canonical form).
    """
    value = (value or "").strip()
    if scheme == "orcid":
        from tessera.connectors.orcid import orcid_checksum_ok
        from tessera.connectors.pubmed import normalize_orcid

        orcid = normalize_orcid(value)
        if not orcid or (strict and not orcid_checksum_ok(orcid)):
            raise InvalidIdentifier(
                f"{value!r} is not a valid ORCID iD (like 0000-0002-1825-0097)."
            )
        return orcid
    if scheme == "email":
        if not _EMAIL.match(value):
            raise InvalidIdentifier(f"{value!r} is not an email address.")
        return value.lower()
    if scheme == "nih_profile":
        if not value.isdigit():
            raise InvalidIdentifier("An NIH profile ID is a number.")
        return value
    if scheme == "local":
        if not value or len(value) > 100:
            raise InvalidIdentifier("A local ID needs 1 to 100 characters.")
        return value
    raise InvalidIdentifier(
        f"Unknown identifier type {scheme!r}; use orcid, email, nih_profile, or local"
    )


def decide_identifier(
    engine: Engine,
    person_id: str,
    scheme: str,
    value: str,
    action: str,
    actor: str,
    note: str | None = None,
) -> dict[str, Any]:
    """Add, confirm, reject, or remove one of a person's identifiers. Logged in person_event.

    A rejected identifier is kept, so that it is never proposed again and counts as evidence
    that papers carrying it belong to someone else.
    """
    if action not in IDENTIFIER_ACTIONS:
        raise InvalidIdentifier(f"Unknown action {action!r}; use add, accept, reject, or remove")
    value = normalize_identifier(scheme, value, strict=action == "add")
    allowed, new_status = IDENTIFIER_ACTIONS[action]
    with engine.begin() as conn:
        if conn.execute(select(person.c.id).where(person.c.id == person_id)).scalar() is None:
            raise TesseraError(f"No person {person_id}")
        where = and_(
            person_identifier.c.person_id == person_id,
            person_identifier.c.scheme == scheme,
            person_identifier.c.value == value,
        )
        current = conn.execute(select(person_identifier.c.status).where(where)).scalar()
        if current not in allowed:
            have = f"is {current}" if current else "is not recorded for this person"
            raise TesseraError(f"Cannot {action} {value}: it {have}.")
        now = utc_now()
        if new_status is None:
            conn.execute(delete(person_identifier).where(where))
        elif current is None:
            add_identifier(conn, person_id, scheme, value, source="curator", status=new_status)
        else:
            conn.execute(
                update(person_identifier).where(where).values(status=new_status, updated_at=now)
            )
        conn.execute(
            insert(person_event).values(
                person_id=person_id,
                actor=actor,
                action="identifier",
                from_status=current,
                to_status=new_status,
                note=note,
                at=now,
                detail={"scheme": scheme, "value": value, "action": action},
            )
        )
    return {"scheme": scheme, "value": value, "status": new_status, "previous": current}


def load_people(
    engine: Engine, statuses: Iterable[str] = ("active", "former")
) -> list[PersonRecord]:
    statuses = tuple(statuses)
    with engine.connect() as conn:
        rows = conn.execute(select(person).where(person.c.status.in_(statuses))).mappings().all()
        people = {r["id"]: PersonRecord(r["id"], r["display_name"], r["status"]) for r in rows}
        for r in conn.execute(select(person_name)).mappings():
            record = people.get(r["person_id"])
            name = make_name(r["family"], r["given"], r["initials"]) if record else None
            if record and name:
                record.names.append(name)
        for r in conn.execute(select(person_identifier)).mappings():
            if record := people.get(r["person_id"]):
                held = {
                    "accepted": record.identifiers,
                    "rejected": record.rejected,
                    "suggested": record.suggested,
                }.get(r["status"])
                if held is not None:
                    held.setdefault(r["scheme"], set()).add(r["value"])
    return list(people.values())


def directory_orcids(engine: Engine, statuses: Iterable[str] = ("active", "former")) -> list[str]:
    """Accepted ORCID iDs of people in the directory, sorted and de-duplicated."""
    query = (
        select(person_identifier.c.value)
        .join(person, person.c.id == person_identifier.c.person_id)
        .where(
            person_identifier.c.scheme == "orcid",
            person_identifier.c.status == "accepted",
            person.c.status.in_(tuple(statuses)),
        )
        .distinct()
    )
    with engine.connect() as conn:
        return sorted(conn.execute(query).scalars())


# --- assertions -----------------------------------------------------------------------------


@dataclass
class Draft:
    """An assertion as automation would like it to be."""

    subject_type: str
    subject_id: str
    predicate: str
    object_type: str
    object_id: str
    origin: str
    method: str
    evidence: list[dict[str, Any]]
    score: float | None = None
    qualifiers: dict[str, Any] = field(default_factory=dict)
    qualifier_key: str = ""
    status: str = "suggested"  # used only when the assertion is new

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.subject_id, self.predicate, self.object_id, self.qualifier_key)


@dataclass
class SyncStats:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    unreported: int = 0
    skipped_low: int = 0

    def to_json(self) -> dict[str, int]:
        return dict(vars(self))


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _same(existing: dict[str, Any], draft: Draft) -> bool:
    old, new = existing["score"], draft.score
    scores_equal = (old is None and new is None) or (
        old is not None and new is not None and math.isclose(old, new, abs_tol=1e-6)
    )
    return (
        scores_equal
        and existing["reported"]
        and _canonical(existing["evidence"]) == _canonical(draft.evidence)
        and _canonical(existing["qualifiers"]) == _canonical(draft.qualifiers)
        and existing["method"] == draft.method
    )


def sync_assertions(
    engine: Engine,
    predicate: str,
    drafts: Iterable[Draft],
    *,
    origin: str,
    min_score_for_new: float | None = None,
    now: datetime | None = None,
    chunk: int = 2000,
    stale_decided: bool = True,
) -> SyncStats:
    """Make the ledger reflect automation's current view of one predicate and origin.

    New assertions are inserted with their draft status. Existing ones get fresh evidence
    and scores; their status changes only if they were never decided by a person (and even
    then automation keeps the current status). Assertions automation no longer produces are
    marked ``reported = false``; none are deleted. With ``stale_decided`` false, decided ones
    keep their flag (a merged pair is decided, not unreported, once its records are one).
    """
    now = now or utc_now()
    stats = SyncStats()
    by_key: dict[tuple[str, str, str, str], Draft] = {}
    for draft in drafts:
        if draft.predicate != predicate or draft.origin != origin:
            raise TesseraError(f"draft {draft.key} does not belong to {predicate}/{origin}")
        current = by_key.get(draft.key)
        if current is None or (draft.score or 0) > (current.score or 0):
            by_key[draft.key] = draft

    columns = [
        assertion.c[c]
        for c in (
            "id",
            "subject_id",
            "predicate",
            "object_id",
            "qualifier_key",
            "score",
            "evidence",
            "qualifiers",
            "method",
            "reported",
            "locked",
            "status",
        )
    ]
    with engine.begin() as conn:
        existing = {
            (r["subject_id"], r["predicate"], r["object_id"], r["qualifier_key"]): dict(r)
            for r in conn.execute(
                select(*columns).where(
                    and_(assertion.c.predicate == predicate, assertion.c.origin == origin)
                )
            ).mappings()
        }
        inserts, updates = [], []
        for key_, draft in by_key.items():
            row = existing.get(key_)
            if row is None:
                if min_score_for_new is not None and (draft.score or 0) < min_score_for_new:
                    stats.skipped_low += 1
                    continue
                inserts.append(
                    {
                        "id": new_id("asr"),
                        "subject_type": draft.subject_type,
                        "subject_id": draft.subject_id,
                        "predicate": predicate,
                        "object_type": draft.object_type,
                        "object_id": draft.object_id,
                        "qualifier_key": draft.qualifier_key,
                        "qualifiers": draft.qualifiers,
                        "origin": origin,
                        "method": draft.method,
                        "score": draft.score,
                        "evidence": draft.evidence,
                        "status": draft.status,
                        "locked": False,
                        "reported": True,
                        "first_seen_at": now,
                        "last_evaluated_at": now,
                        "updated_at": now,
                    }
                )
            elif _same(row, draft):
                stats.unchanged += 1
            else:
                updates.append(
                    {
                        "_id": row["id"],
                        "score": draft.score,
                        "evidence": draft.evidence,
                        "qualifiers": draft.qualifiers,
                        "method": draft.method,
                        "reported": True,
                        "last_evaluated_at": now,
                        "updated_at": now,
                    }
                )
        stale = [
            row["id"]
            for key_, row in existing.items()
            if key_ not in by_key and row["reported"] and (stale_decided or not row["locked"])
        ]

        for start in range(0, len(inserts), chunk):
            conn.execute(insert(assertion), inserts[start : start + chunk])
        if updates:
            statement = (
                update(assertion)
                .where(assertion.c.id == bindparam("_id"))
                .values(
                    score=bindparam("score"),
                    evidence=bindparam("evidence"),
                    qualifiers=bindparam("qualifiers"),
                    method=bindparam("method"),
                    reported=bindparam("reported"),
                    last_evaluated_at=bindparam("last_evaluated_at"),
                    updated_at=bindparam("updated_at"),
                )
            )
            for start in range(0, len(updates), chunk):
                conn.execute(statement, updates[start : start + chunk])
        for start in range(0, len(stale), chunk):
            conn.execute(
                update(assertion)
                .where(assertion.c.id.in_(stale[start : start + chunk]))
                .values(reported=False, updated_at=now)
            )
    stats.inserted, stats.updated, stats.unreported = len(inserts), len(updates), len(stale)
    return stats


ACTIONS = {"accept": "accepted", "reject": "rejected", "defer": "deferred"}


def decide(
    engine: Engine, assertion_id: str, action: str, actor: str, note: str | None = None
) -> dict[str, Any]:
    """Apply a person's decision. ``undo`` restores the status before the latest decision."""
    with engine.begin() as conn:
        row = (
            conn.execute(select(assertion).where(assertion.c.id == assertion_id)).mappings().first()
        )
        if row is None:
            raise TesseraError(f"No assertion {assertion_id}")
        if row["predicate"] == "same_as" and action == "accept":
            raise TesseraError("To accept a possible duplicate, merge the two records.")
        if action == "undo":
            events = (
                conn.execute(
                    select(curation_event)
                    .where(curation_event.c.assertion_id == assertion_id)
                    .order_by(curation_event.c.id)
                )
                .mappings()
                .all()
            )
            stack: list[Any] = []
            for event in events:
                if event["action"] == "undo":
                    if stack:
                        stack.pop()
                else:
                    stack.append(event)
            if not stack:
                raise TesseraError("Nothing to undo for this assertion.")
            new_status = stack[-1]["from_status"] or "suggested"
            locked = new_status in {"accepted", "rejected"}
        elif action in ACTIONS:
            new_status = ACTIONS[action]
            locked = action in {"accept", "reject"}
        else:
            raise TesseraError(f"Unknown action {action!r}; use accept, reject, defer, or undo")
        now = utc_now()
        conn.execute(
            update(assertion)
            .where(assertion.c.id == assertion_id)
            .values(
                status=new_status,
                locked=locked,
                decided_by=actor,
                decided_at=now,
                decision_note=note,
                updated_at=now,
            )
        )
        conn.execute(
            insert(curation_event).values(
                assertion_id=assertion_id,
                actor=actor,
                action=action,
                from_status=row["status"],
                to_status=new_status,
                note=note,
                at=now,
            )
        )
    return {"id": assertion_id, "status": new_status, "locked": locked}


def assertions_for(
    engine: Engine,
    *,
    predicate: str | None = None,
    subject_id: str | None = None,
    object_id: str | None = None,
    statuses: Iterable[str] | None = None,
    reported_only: bool = True,
    min_score: float | None = None,
) -> list[dict[str, Any]]:
    query = select(assertion)
    if min_score is not None:
        query = query.where(assertion.c.score >= min_score)
    if predicate:
        query = query.where(assertion.c.predicate == predicate)
    if subject_id:
        query = query.where(assertion.c.subject_id == subject_id)
    if object_id:
        query = query.where(assertion.c.object_id == object_id)
    if statuses:
        query = query.where(assertion.c.status.in_(tuple(statuses)))
    if reported_only:
        query = query.where(assertion.c.reported.is_(True))
    # In the order they were made (IDs sort by time), the same on SQLite and PostgreSQL.
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(query.order_by(assertion.c.id)).mappings()]


def display_names(engine: Engine, person_ids: Iterable[str]) -> dict[str, str]:
    ids = sorted(set(person_ids))
    out: dict[str, str] = {}
    with engine.connect() as conn:
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            out.update(
                conn.execute(
                    select(person.c.id, person.c.display_name).where(person.c.id.in_(chunk))
                ).all()
            )
    return out


def people_by_identifier(engine: Engine, scheme: str, values: Iterable[str]) -> dict[str, str]:
    """Map identifier values (e.g. NIH profile IDs) to person IDs, following merges."""
    wanted = sorted({str(v) for v in values})
    out: dict[str, str] = {}
    with engine.connect() as conn:
        for start in range(0, len(wanted), 500):
            chunk = wanted[start : start + 500]
            rows = conn.execute(
                select(person_identifier.c.value, person_identifier.c.person_id, person.c.status)
                .join(person, person.c.id == person_identifier.c.person_id)
                .where(
                    and_(
                        person_identifier.c.scheme == scheme,
                        person_identifier.c.value.in_(chunk),
                        person_identifier.c.status != "rejected",
                    )
                )
                .order_by(person_identifier.c.status.desc())  # accepted last, so it wins
            ).all()
            for value, person_id, status in rows:
                out[value] = survivor_of(conn, person_id) if status == "merged" else person_id
    return out
