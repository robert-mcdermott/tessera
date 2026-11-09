"""Merging two records of the same person, and undoing a merge exactly (REC-07).

A merge folds the *merged* record into the *survivor*: its links (assertions), names, and
identifiers move to the survivor, and the merged record keeps a pointer (``merged_into``) and
the status ``merged``. Every change is written to a journal in order, with the values it
replaced, so an unmerge replays the journal backwards and leaves the ledger as it was.

When both records hold the same link (the same paper, grant, or unit), one copy goes to the
survivor and the other stays with the merged record, out of sight:

* a human decision beats a suggestion;
* between two decisions, the most recent wins (reported as a conflict, because the two
  records were decided differently);
* otherwise the survivor's copy stays.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, insert, or_, select, update
from sqlalchemy.engine import Connection, Engine

from tessera.core.clock import utc_now
from tessera.core.errors import TesseraError
from tessera.core.ids import new_id
from tessera.ledger.tables import (
    assertion,
    person,
    person_event,
    person_identifier,
    person_merge,
    person_name,
)

TABLES = {
    "assertion": assertion,
    "person": person,
    "person_name": person_name,
    "person_identifier": person_identifier,
}
SAME_AS = "same_as"


class MergeError(TesseraError):
    """A merge or unmerge that cannot be done as asked."""


def _encode(value: Any) -> Any:
    return {"$dt": value.isoformat()} if isinstance(value, datetime) else value


def _decode(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"$dt"}:
        return datetime.fromisoformat(value["$dt"])
    return value


def _unchanged(current: dict[str, Any], after: dict[str, Any]) -> bool:
    """Whether a row still holds what the merge left in it."""
    for column, value in after.items():
        now, then = current.get(column), _decode(value)
        if isinstance(now, datetime) and isinstance(then, datetime):
            if now.replace(tzinfo=None) != then.replace(tzinfo=None):
                return False
        elif now != then:
            return False
    return True


class Journal:
    """The ordered list of changes a merge makes, each with the values it replaced."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn
        self.entries: list[dict[str, Any]] = []

    def change(self, table: str, row_id: Any, **values: Any) -> None:
        t = TABLES[table]
        before = (
            self.conn.execute(select(*(t.c[c] for c in values)).where(t.c.id == row_id))
            .mappings()
            .one()
        )
        self.entries.append(
            {
                "op": "update",
                "table": table,
                "id": row_id,
                "before": {k: _encode(v) for k, v in before.items()},
                "after": {k: _encode(v) for k, v in values.items()},
            }
        )
        self.conn.execute(update(t).where(t.c.id == row_id).values(**values))

    def created(self, table: str, row_id: Any) -> None:
        self.entries.append({"op": "insert", "table": table, "id": row_id})


def _person(conn: Connection, person_id: str) -> dict[str, Any]:
    row = conn.execute(select(person).where(person.c.id == person_id)).mappings().first()
    if row is None:
        raise MergeError(f"No person {person_id}")
    return dict(row)


def _key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (row["predicate"], row["object_id"], row["qualifier_key"])


EARLIEST = datetime.min.replace(tzinfo=UTC)


def _merged_copy_wins(mine: dict[str, Any], theirs: dict[str, Any]) -> bool:
    """Whether the merged record's copy of a link should replace the survivor's copy."""
    if mine["locked"] != theirs["locked"]:
        return bool(mine["locked"])
    if mine["locked"] and mine["status"] != theirs["status"]:
        return (mine["decided_at"] or EARLIEST) > (theirs["decided_at"] or EARLIEST)
    return False


def merge_people(
    engine: Engine,
    merged_id: str,
    survivor_id: str,
    actor: str,
    note: str | None = None,
) -> dict[str, Any]:
    """Fold ``merged_id`` into ``survivor_id``. Returns what moved and any conflicts."""
    if merged_id == survivor_id:
        raise MergeError("A person cannot be merged with themselves.")
    now = utc_now()
    merge_id = new_id("mrg")
    holding = f"merging:{merge_id}"  # a transient owner while two links swap places
    summary: dict[str, Any] = {"links": 0, "names": 0, "identifiers": 0, "conflicts": []}
    with engine.begin() as conn:
        merged, survivor = _person(conn, merged_id), _person(conn, survivor_id)
        for p in (merged, survivor):
            if p["status"] == "merged":
                raise MergeError(
                    f"{p['display_name']} has already been merged into another record."
                )
        journal = Journal(conn)

        # The proposal between the two records, if any, records the decision.
        pair = or_(
            and_(assertion.c.subject_id == merged_id, assertion.c.object_id == survivor_id),
            and_(assertion.c.subject_id == survivor_id, assertion.c.object_id == merged_id),
        )
        proposal = (
            conn.execute(select(assertion).where(assertion.c.predicate == SAME_AS, pair))
            .mappings()
            .first()
        )
        if proposal is not None:
            journal.change(
                "assertion",
                proposal["id"],
                status="accepted",
                locked=True,
                decided_by=actor,
                decided_at=now,
                decision_note=note,
                updated_at=now,
            )
        else:
            proposal_id = new_id("asr")
            conn.execute(
                insert(assertion).values(
                    id=proposal_id,
                    subject_type="person",
                    subject_id=merged_id,
                    predicate=SAME_AS,
                    object_type="person",
                    object_id=survivor_id,
                    qualifier_key="",
                    qualifiers={},
                    origin="human",
                    method="manual",
                    score=None,
                    evidence=[],
                    status="accepted",
                    locked=True,
                    reported=True,
                    decided_by=actor,
                    decided_at=now,
                    decision_note=note,
                    first_seen_at=now,
                    last_evaluated_at=now,
                    updated_at=now,
                )
            )
            journal.created("assertion", proposal_id)

        # Links: the merged record's assertions move to the survivor.
        theirs = {
            _key(dict(r)): dict(r)
            for r in conn.execute(
                select(assertion).where(
                    assertion.c.subject_id == survivor_id, assertion.c.predicate != SAME_AS
                )
            ).mappings()
        }
        mine = [
            dict(r)
            for r in conn.execute(
                select(assertion)
                .where(assertion.c.subject_id == merged_id, assertion.c.predicate != SAME_AS)
                .order_by(assertion.c.id)
            ).mappings()
        ]
        for row in mine:
            other = theirs.get(_key(row))
            if other is None:
                journal.change("assertion", row["id"], subject_id=survivor_id)
                summary["links"] += 1
                continue
            if row["locked"] and other["locked"] and row["status"] != other["status"]:
                summary["conflicts"].append(
                    {
                        "predicate": row["predicate"],
                        "object_id": row["object_id"],
                        "merged_status": row["status"],
                        "survivor_status": other["status"],
                    }
                )
            if _merged_copy_wins(row, other):
                journal.change("assertion", row["id"], subject_id=holding)
                journal.change("assertion", other["id"], subject_id=merged_id)
                journal.change("assertion", row["id"], subject_id=survivor_id)
                summary["links"] += 1

        # Other people's proposals that point at the merged record now point at the survivor.
        for row in conn.execute(
            select(assertion.c.id, assertion.c.subject_id).where(
                assertion.c.predicate == SAME_AS,
                assertion.c.object_id == merged_id,
                assertion.c.subject_id != survivor_id,
            )
        ).mappings():
            clash = conn.execute(
                select(assertion.c.id).where(
                    assertion.c.predicate == SAME_AS,
                    assertion.c.subject_id == row["subject_id"],
                    assertion.c.object_id == survivor_id,
                )
            ).first()
            if clash is None:
                journal.change("assertion", row["id"], object_id=survivor_id)

        # Names: kept as variants of the survivor, unless the survivor already has the name.
        have = {
            (r["family"], r["given"])
            for r in conn.execute(
                select(person_name.c.family, person_name.c.given).where(
                    person_name.c.person_id == survivor_id
                )
            ).mappings()
        }
        for r in conn.execute(
            select(person_name)
            .where(person_name.c.person_id == merged_id)
            .order_by(person_name.c.id)
        ).mappings():
            if (r["family"], r["given"]) in have:
                continue
            have.add((r["family"], r["given"]))
            journal.change("person_name", r["id"], person_id=survivor_id, kind="variant")
            summary["names"] += 1

        # Identifiers move too; where both hold one, a confirmation on either side holds.
        held = {
            (r["scheme"], r["value"]): dict(r)
            for r in conn.execute(
                select(person_identifier).where(person_identifier.c.person_id == survivor_id)
            ).mappings()
        }
        for r in conn.execute(
            select(person_identifier)
            .where(person_identifier.c.person_id == merged_id)
            .order_by(person_identifier.c.id)
        ).mappings():
            existing = held.get((r["scheme"], r["value"]))
            if existing is None:
                journal.change("person_identifier", r["id"], person_id=survivor_id)
                summary["identifiers"] += 1
            elif r["status"] == "accepted" and existing["status"] == "suggested":
                journal.change(
                    "person_identifier", existing["id"], status="accepted", updated_at=now
                )

        journal.change(
            "person", merged_id, status="merged", merged_into=survivor_id, updated_at=now
        )
        conn.execute(
            insert(person_merge).values(
                id=merge_id,
                merged_id=merged_id,
                survivor_id=survivor_id,
                actor=actor,
                note=note,
                at=now,
                journal=journal.entries,
                summary=summary,
            )
        )
        conn.execute(
            insert(person_event).values(
                person_id=merged_id,
                actor=actor,
                action="merge",
                from_status=merged["status"],
                to_status="merged",
                note=note,
                at=now,
                detail={"merge_id": merge_id, "survivor_id": survivor_id},
            )
        )
    return {"merge_id": merge_id, "merged_id": merged_id, "survivor_id": survivor_id, **summary}


def unmerge(engine: Engine, merge_id: str, actor: str, note: str | None = None) -> dict[str, Any]:
    """Undo a merge by replaying its journal backwards."""
    now = utc_now()
    with engine.begin() as conn:
        row = (
            conn.execute(select(person_merge).where(person_merge.c.id == merge_id))
            .mappings()
            .first()
        )
        if row is None:
            raise MergeError(f"No merge {merge_id}")
        if row["undone_at"] is not None:
            raise MergeError("This merge has already been undone.")
        merged, survivor = _person(conn, row["merged_id"]), _person(conn, row["survivor_id"])
        if survivor["status"] == "merged":
            raise MergeError(
                f"{survivor['display_name']} was itself merged into another record later. "
                "Undo that merge first."
            )
        later = conn.execute(
            select(person_merge.c.id).where(
                person_merge.c.survivor_id == row["survivor_id"],
                person_merge.c.undone_at.is_(None),
                person_merge.c.at > row["at"],
            )
        ).first()
        if later is not None:  # its journal assumes this one's changes are still in place
            raise MergeError(
                f"Another record has been merged into {survivor['display_name']} since. "
                "Undo that merge first."
            )
        for entry in reversed(row["journal"]):
            t = TABLES[entry["table"]]
            if entry["op"] == "insert":
                conn.execute(delete(t).where(t.c.id == entry["id"]))
                continue
            current = (
                conn.execute(select(*(t.c[c] for c in entry["after"])).where(t.c.id == entry["id"]))
                .mappings()
                .first()
            )
            if current is None:
                raise MergeError(
                    "Something this merge changed no longer exists; it cannot be undone."
                )
            if not _unchanged(dict(current), entry["after"]):
                # Changed since (a decision on a moved link, say): putting back what was there
                # before the merge would quietly undo that too.
                raise MergeError(
                    "Something this merge changed has been changed again since (a decision, "
                    "say), so it cannot be undone automatically."
                )
            conn.execute(
                update(t)
                .where(t.c.id == entry["id"])
                .values(**{k: _decode(v) for k, v in entry["before"].items()})
            )
        conn.execute(
            update(person_merge)
            .where(person_merge.c.id == merge_id)
            .values(undone_at=now, undone_by=actor)
        )
        conn.execute(
            insert(person_event).values(
                person_id=row["merged_id"],
                actor=actor,
                action="unmerge",
                from_status="merged",
                to_status=_decode(
                    next(
                        e["before"]["status"]
                        for e in row["journal"]
                        if e["table"] == "person" and e["id"] == row["merged_id"]
                    )
                ),
                note=note,
                at=now,
                detail={"merge_id": merge_id, "survivor_id": row["survivor_id"]},
            )
        )
    return {
        "merge_id": merge_id,
        "merged_id": merged["id"],
        "survivor_id": survivor["id"],
    }


def active_merges(engine: Engine, person_id: str) -> list[dict[str, Any]]:
    """Merges into this person, and the merge that folded this person away, if any."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(person_merge, person.c.display_name)
            .join(person, person.c.id == person_merge.c.merged_id)
            .where(
                person_merge.c.undone_at.is_(None),
                or_(
                    person_merge.c.survivor_id == person_id,
                    person_merge.c.merged_id == person_id,
                ),
            )
            .order_by(person_merge.c.at)
        ).mappings()
        return [
            {
                "merge_id": r["id"],
                "merged_id": r["merged_id"],
                "merged_name": r["display_name"],
                "survivor_id": r["survivor_id"],
                "actor": r["actor"],
                "note": r["note"],
                "at": r["at"],
                "summary": r["summary"],
            }
            for r in rows
        ]


__all__ = ["MergeError", "active_merges", "merge_people", "unmerge"]
