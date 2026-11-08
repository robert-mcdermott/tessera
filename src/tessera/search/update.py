"""Bring the search index up to date after decisions, without a rebuild.

A decision changes a few documents: the record it links (its member list and person
facets), the person's counts, and, when a person is promoted or dismissed, whether that
person is in the directory at all. ``apply`` rewrites just those documents from the
ledger, using the same member rules as the full build, so lists, facets, and counts are
right on the next request; so are the person's current groups, after a membership changes
or a group is renamed or archived. Topics and other figures that depend on the whole population
refresh at the next full build (``tessera build search``, also run by ``sync``).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from tessera.config import Config
from tessera.core.clock import isoformat, utc_now
from tessera.evidence.memberships import is_current
from tessera.home import DataHome
from tessera.lake import Lake
from tessera.ledger.tables import assertion, person, person_identifier, person_name, unit
from tessera.search.index import (
    ALL_KINDS,
    LINKS,
    MEMBER_STATUSES,
    Doc,
    _Builder,
    candidate_ref,
    counts_as_member_link,
    index_path,
    insert_doc,
    member_entry,
    sort_members,
)

log = logging.getLogger("tessera.search")

PREDICATE_BY_PREFIX = {"pub:": "authored", "grt:": "investigator_on", "stu:": "official_of"}


def _predicate(ref: str) -> str | None:
    return next((p for prefix, p in PREDICATE_BY_PREFIX.items() if ref.startswith(prefix)), None)


def records_of_people(engine: Engine, person_ids: Iterable[str]) -> set[str]:
    """Every record a person is linked to, so their documents can follow a status change."""
    ids = list(person_ids)
    if not ids:
        return set()
    with engine.connect() as conn:
        rows = conn.execute(
            select(assertion.c.object_id).where(
                assertion.c.subject_id.in_(ids), assertion.c.predicate.in_(tuple(LINKS))
            )
        ).all()
    return {r[0] for r in rows}


def apply(
    config: Config,
    home: DataHome,
    engine: Engine,
    *,
    people: Iterable[str] = (),
    records: Iterable[str] = (),
) -> dict[str, int] | None:
    """Rewrite the documents for these people and records (refs such as ``pub:123``).

    Returns how many documents changed, or None when there is no index to update.
    """
    path = index_path(home)
    if not path.is_file():
        return None
    person_ids = set(people)
    refs = {r for r in records if _predicate(r)}
    review = config.evidence.review_threshold
    with engine.connect() as conn:
        member_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        if refs:
            query = (
                select(assertion, person.c.display_name)
                .join(person, person.c.id == assertion.c.subject_id)
                .where(
                    assertion.c.object_id.in_(sorted(refs)),
                    assertion.c.predicate.in_(tuple(LINKS)),
                    assertion.c.reported.is_(True),
                    person.c.status.in_(MEMBER_STATUSES),
                )
            )
            for row in conn.execute(query).mappings():
                row = dict(row)
                if row["predicate"] == _predicate(row["object_id"]) and counts_as_member_link(
                    row, review
                ):
                    member_rows[row["object_id"]].append(row)
        people_rows = {
            r["id"]: dict(r)
            for r in conn.execute(select(person).where(person.c.id.in_(sorted(person_ids))))
            .mappings()
            .all()
        }
        for pid, family, given in conn.execute(
            select(person_name.c.person_id, person_name.c.family, person_name.c.given).where(
                person_name.c.person_id.in_(sorted(people_rows))
            )
        ):
            people_rows[pid].setdefault("variants", []).append(
                " ".join(part for part in (given, family) if part)
            )
        for pid, value in conn.execute(
            select(person_identifier.c.person_id, person_identifier.c.value).where(
                person_identifier.c.person_id.in_(sorted(people_rows)),
                person_identifier.c.scheme == "orcid",
                person_identifier.c.status == "accepted",
            )
        ):
            people_rows[pid].setdefault("orcid", value)
        units_by_person: dict[str, list[str]] = defaultdict(list)
        if person_ids:  # current memberships of active groups, as in a full build
            today = date.today()
            for subject, name, qualifiers in conn.execute(
                select(assertion.c.subject_id, unit.c.name, assertion.c.qualifiers)
                .join(unit, unit.c.id == assertion.c.object_id)
                .where(
                    assertion.c.subject_id.in_(sorted(person_ids)),
                    assertion.c.predicate == "member_of",
                    assertion.c.status == "accepted",
                    unit.c.status == "active",
                )
            ):
                if is_current(qualifiers, today):
                    units_by_person[subject].append(name)
        links_by_person: dict[str, list[dict[str, Any]]] = defaultdict(list)
        if person_ids:
            for row in conn.execute(
                select(assertion).where(
                    assertion.c.subject_id.in_(sorted(person_ids)),
                    assertion.c.predicate.in_(tuple(LINKS)),
                    assertion.c.reported.is_(True),
                )
            ).mappings():
                if counts_as_member_link(dict(row), review):
                    links_by_person[row["subject_id"]].append(dict(row))

    changed = 0
    con = sqlite3.connect(path, timeout=15)
    try:
        con.execute("BEGIN IMMEDIATE")
        existing = _docs(con, refs | person_ids | {candidate_ref(p) for p in person_ids})
        missing_pubs: set[str] = set()
        for ref in refs:
            predicate = _predicate(ref) or ""
            kind, key = LINKS[predicate]
            entries = sort_members(
                predicate,
                [member_entry(predicate, r, r["display_name"]) for r in member_rows.get(ref, [])],
            )
            doc = existing.get((kind, ref))
            if doc is None:
                if kind == "publication" and entries:
                    missing_pubs.add(ref)
                continue
            summary = json.loads(doc["summary"])
            if summary.get(key) != entries:
                summary[key] = entries
                _set_summary(con, doc["id"], summary)
                _set_facets(con, doc["id"], "person", [e["id"] for e in entries])
                changed += 1
        if missing_pubs:  # e.g. a paper added by hand that was not organization-relevant yet
            changed += _add_publications(config, home, engine, con, missing_pubs, member_rows)
        for person_id in person_ids:
            row = people_rows.get(person_id)
            changed += _update_person(
                con,
                existing,
                person_id,
                row,
                links_by_person[person_id],
                sorted(set(units_by_person[person_id])),
            )
        if changed:
            _refresh_meta(con)
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()
    return {"changed": changed}


def _docs(con: sqlite3.Connection, refs: set[str]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    items = sorted(refs)
    for start in range(0, len(items), 500):
        chunk = items[start : start + 500]
        marks = ", ".join("?" for _ in chunk)
        for doc_id, kind, ref, summary, weight in con.execute(
            f"SELECT id, kind, ref, summary, weight FROM doc WHERE ref IN ({marks})", chunk
        ):
            out[(kind, ref)] = {"id": doc_id, "summary": summary, "weight": weight}
    return out


def _set_summary(con: sqlite3.Connection, doc_id: int, summary: dict[str, Any], **extra) -> None:  # type: ignore[no-untyped-def]
    sets = ", ".join(["summary = ?", *(f"{k} = ?" for k in extra)])
    con.execute(
        f"UPDATE doc SET {sets} WHERE id = ?",
        [json.dumps(summary, default=str), *extra.values(), doc_id],
    )


def _set_facets(con: sqlite3.Connection, doc_id: int, name: str, values: Iterable[str]) -> None:
    con.execute("DELETE FROM facet WHERE doc_id = ? AND name = ?", (doc_id, name))
    con.executemany(
        "INSERT INTO facet VALUES (?, ?, ?)", [(doc_id, name, v) for v in dict.fromkeys(values)]
    )


def _delete_doc(con: sqlite3.Connection, doc_id: int) -> None:
    con.execute("DELETE FROM facet WHERE doc_id = ?", (doc_id,))
    con.execute("DELETE FROM doc_fts WHERE rowid = ?", (doc_id,))
    con.execute("DELETE FROM doc WHERE id = ?", (doc_id,))


def _next_id(con: sqlite3.Connection) -> int:
    return (con.execute("SELECT coalesce(max(id), 0) FROM doc").fetchone()[0] or 0) + 1


def _add_publications(
    config: Config,
    home: DataHome,
    engine: Engine,
    con: sqlite3.Connection,
    refs: set[str],
    member_rows: dict[str, list[dict[str, Any]]],
) -> int:
    members = {
        ref: sort_members(
            "authored", [member_entry("authored", r, r["display_name"]) for r in member_rows[ref]]
        )
        for ref in refs
    }
    with Lake(home.lake) as lake:
        builder = _Builder(config, home, engine, lake)
        builder.publication_docs({r.removeprefix("pub:") for r in refs}, members)
    next_id = _next_id(con)
    for doc in builder.docs:
        insert_doc(con, next_id, doc)
        next_id += 1
    return len(builder.docs)


def _person_counts(links: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"accepted": 0, "suggested": 0, "grants": 0, "studies": 0}
    for row in links:
        if row["predicate"] == "authored":
            counts["accepted" if row["status"] == "accepted" else "suggested"] += 1
        elif row["predicate"] == "investigator_on":
            counts["grants"] += 1
        elif row["predicate"] == "official_of":
            counts["studies"] += 1
    return counts


def _update_person(
    con: sqlite3.Connection,
    existing: dict[tuple[str, str], dict[str, Any]],
    person_id: str,
    row: dict[str, Any] | None,
    links: list[dict[str, Any]],
    units: list[str],
) -> int:
    changed = 0
    status = row["status"] if row else None
    candidate = existing.get(("candidate", candidate_ref(person_id)))
    if candidate is not None and status == "merged":  # as in a full build: merged records
        _delete_doc(con, candidate["id"])  # leave the discovered-people list
        changed += 1
        candidate = None
    if candidate is not None and status:
        summary = json.loads(candidate["summary"])
        if summary.get("status") != status:
            summary["status"] = status
            _set_summary(con, candidate["id"], summary)
            _set_facets(con, candidate["id"], "status", [status])
            changed += 1
    doc = existing.get(("person", person_id))
    if status not in MEMBER_STATUSES:
        if doc is not None:  # no longer in the directory
            _delete_doc(con, doc["id"])
            changed += 1
        return changed
    counts = _person_counts(links)
    if doc is not None:
        summary = json.loads(doc["summary"])
        if (
            summary.get("counts") != counts
            or summary.get("status") != status
            or summary.get("units") != units
        ):
            summary["counts"] = counts
            summary["status"] = status
            summary["units"] = units
            _set_summary(
                con, doc["id"], summary, weight=float(counts["accepted"] + counts["suggested"])
            )
            _set_facets(con, doc["id"], "status", [status])
            _set_facets(con, doc["id"], "unit", units)
            changed += 1
        return changed
    # Newly in the directory (promoted): a plain document until the next full build adds
    # topics.
    assert row is not None
    summary = {
        "id": person_id,
        "name": row["display_name"],
        "status": status,
        "units": units,
        "orcid": row.get("orcid"),
        "counts": counts,
        "concepts": [],
    }
    insert_doc(
        con,
        _next_id(con),
        Doc(
            "person",
            person_id,
            row["display_name"],
            None,
            None,
            sort_key=f"{(row['family_name'] or row['display_name']).lower()} "
            f"{row['display_name'].lower()}",
            weight=float(counts["accepted"] + counts["suggested"]),
            summary=summary,
            people="; ".join(row.get("variants") or [row["display_name"]]),
            facets=[("status", status), *(("unit", u) for u in units)],
        ),
    )
    return changed + 1


def _refresh_meta(con: sqlite3.Connection) -> None:
    counts = dict(con.execute("SELECT kind, count(*) FROM doc GROUP BY kind").fetchall())
    con.executemany(
        "INSERT OR REPLACE INTO meta VALUES (?, ?)",
        [
            ("updated_at", isoformat(utc_now())),
            *((f"count:{k}", str(counts.get(k, 0))) for k in ALL_KINDS),
        ],
    )


def refresh_opportunities(home: DataHome, engine: Engine, refs: Iterable[str]) -> int | None:
    """Rewrite the documents of these opportunities (``opp:<id>``) after a change to the
    organization's own. Returns how many were written, or None when there is no index."""
    from tessera.opportunities.catalog import some
    from tessera.search.index import opportunity_doc

    path = index_path(home)
    if not path.is_file():
        return None
    wanted = sorted(set(refs))
    today = date.today()
    with Lake(home.lake) as lake:
        views = {v["id"]: v for v in some(engine, lake, wanted, today)}
    con = sqlite3.connect(path, timeout=15)
    try:
        for found in _docs(con, set(wanted)).values():
            _delete_doc(con, found["id"])
        next_id = _next_id(con)
        for ref in wanted:
            if ref in views:
                insert_doc(con, next_id, opportunity_doc(views[ref], today))
                next_id += 1
        _refresh_meta(con)
        con.commit()
    finally:
        con.close()
    return sum(1 for ref in wanted if ref in views)
