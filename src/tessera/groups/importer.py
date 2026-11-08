"""Groups and memberships from a file or a feed, keyed by the source's own identifiers.

An import names its **source** (``hr``, ``registry``, or any short name). Each group row
carries the source's identifier for it (``id``), so importing the same source again updates
the groups it made instead of adding duplicates, and a group merged by hand is still found
through the one it was merged into. Rows without an identifier are matched by code, then by
name. A group made by hand is adopted by the first import whose row matches it by code or
name and gives an identifier.

**Groups** (CSV columns or JSON keys): ``id``, ``name`` (required), ``kind``, ``code``,
``short_name``, ``description``, ``status`` (``active`` or ``archived``), ``slug`` (used only
when the group is created), and ``parents``: the identifiers (in this source), codes, or
addresses of the groups it sits under, separated by ``;`` in CSV.

**Memberships**: ``group`` (identifier in this source, code, address, or Tessera ID), the
person (``person`` as a Tessera ID, or ``orcid``, ``email``, or ``local_id``), ``role``,
``title``, ``start``, ``end``, and ``id`` (the source's identifier for the membership). People
are not created here; the roster adds people.

A CSV file holds one or the other (a ``group`` column makes it memberships). A JSON file is
``{"groups": [...], "memberships": [...]}``.

The source is authoritative for what it supplies: an import overwrites the fields its rows
give, and replaces the parent links it made earlier (links made by hand stay). A membership
removed by hand stays removed. With ``complete``, the import is the source's whole list:
its groups that are missing are archived, and its current memberships that are missing end
today (their people become alumni). ``dry_run`` reports what would change and changes
nothing.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.engine import Connection, Engine

from tessera.config import VocabularyConfig
from tessera.core.clock import utc_now
from tessera.core.errors import TesseraError
from tessera.evidence import store as people
from tessera.evidence.memberships import is_current
from tessera.groups import store
from tessera.groups.store import GroupError, _event
from tessera.ledger.tables import assertion, person, unit

GROUP_COLUMNS = {
    "id": ("id", "external_id", "group_id"),
    "name": ("name", "group_name"),
    "kind": ("kind", "type", "group_kind"),
    "code": ("code", "group_code"),
    "short_name": ("short_name", "abbreviation", "acronym"),
    "description": ("description", "summary", "mission"),
    "email": ("email", "contact_email"),
    "website": ("website", "url", "homepage"),
    "status": ("status",),
    "slug": ("slug", "address"),
    "parents": ("parents", "parent", "parent_id", "parent_ids"),
}
MEMBERSHIP_COLUMNS = {
    "id": ("id", "external_id", "membership_id"),
    "group": ("group", "group_id", "group_code"),
    "person": ("person", "person_id", "tessera_id"),
    "orcid": ("orcid", "orcid_id"),
    "email": ("email", "email_address"),
    "local_id": ("local_id", "employee_id"),
    "role": ("role",),
    "title": ("title", "position"),
    "start": ("start", "start_date", "from", "since"),
    "end": ("end", "end_date", "to", "until"),
}
MAX_ROWS = 50_000


@dataclass
class ImportStats:
    source: str
    dry_run: bool = False
    complete: bool = False
    groups: Counter = field(default_factory=Counter)
    memberships: Counter = field(default_factory=Counter)
    skipped: list[str] = field(default_factory=list)
    people: set[str] = field(default_factory=set)  # whose memberships changed

    def to_json(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "dry_run": self.dry_run,
            "complete": self.complete,
            "groups": dict(self.groups),
            "memberships": dict(self.memberships),
            "skipped": self.skipped[:50],
            "skipped_count": len(self.skipped),
        }


def _normalize(rows: list[dict[str, Any]], columns: dict[str, tuple[str, ...]]) -> list[dict]:
    out = []
    for row in rows:
        lowered = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
        item: dict[str, Any] = {}
        for name, aliases in columns.items():
            for alias in aliases:
                if alias in lowered and lowered[alias] not in (None, ""):
                    value = lowered[alias]
                    item[name] = value.strip() if isinstance(value, str) else value
                    break
        if "parents" in columns and any(a in lowered for a in columns["parents"]):
            item.setdefault("parents", [])  # the column is there: an empty cell means none
        out.append(item)
    return out


def parse_rows(data: Any) -> dict[str, list[dict[str, Any]]]:
    """``{"groups": [...], "memberships": [...]}`` (a list alone is a list of groups)."""
    if isinstance(data, list):
        data = {"groups": data}
    if not isinstance(data, dict):
        raise TesseraError("An import is an object with 'groups' and 'memberships' lists.")
    unknown = set(data) - {"groups", "memberships", "source"}
    if unknown:
        raise TesseraError(f"Unknown key {sorted(unknown)[0]!r} in the import.")
    out: dict[str, list[dict[str, Any]]] = {}
    for key, columns in (("groups", GROUP_COLUMNS), ("memberships", MEMBERSHIP_COLUMNS)):
        rows = data.get(key) or []
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise TesseraError(f"'{key}' must be a list of objects.")
        if len(rows) > MAX_ROWS:
            raise TesseraError(f"At most {MAX_ROWS:,} {key} in one import.")
        out[key] = _normalize(rows, columns)
    return out


def read_file(path: Path) -> dict[str, list[dict[str, Any]]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise TesseraError(f"Cannot read {path}: {exc}") from exc
    if path.suffix.lower() == ".json":
        try:
            return parse_rows(json.loads(text))
        except json.JSONDecodeError as exc:
            raise TesseraError(f"{path} is not valid JSON: {exc}") from exc
    reader = csv.DictReader(text.splitlines())
    headers = {h.strip().lower() for h in reader.fieldnames or []}
    rows = list(reader)
    if headers & set(MEMBERSHIP_COLUMNS["group"]):
        return parse_rows({"memberships": rows})
    if not headers & set(GROUP_COLUMNS["name"]):
        raise TesseraError(
            f"{path} needs a 'name' column (groups) or a 'group' column (memberships)."
        )
    return parse_rows({"groups": rows})


def _parents(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in str(value or "").replace("|", ";").split(";") if p.strip()]


def _group_ref(conn: Connection, ref: str, source: str, made: dict[str, str]) -> str | None:
    if ref in made:
        return made[ref]
    found = store.find_for_import(conn, source=source, external_id=ref, code=None, name=None)
    if found:
        return found["id"]
    try:
        return store.resolve(conn, ref)["id"]
    except store.GroupNotFound:
        return None


def _person(conn: Connection, row: dict[str, Any]) -> str | None:
    if row.get("person"):
        found = conn.execute(select(person.c.id).where(person.c.id == row["person"])).scalar()
        return people.survivor_of(conn, found) if found else None
    for scheme, key in (("orcid", "orcid"), ("email", "email"), ("local", "local_id")):
        if row.get(key):
            try:
                value = people.normalize_identifier(scheme, str(row[key]), strict=False)
            except TesseraError:
                continue
            found = people.find_by_identifier(conn, scheme, value)
            if found:
                return people.survivor_of(conn, found)
    return None


def _import_groups(
    conn: Connection,
    rows: list[dict[str, Any]],
    stats: ImportStats,
    *,
    source: str,
    actor: str,
    vocabulary: VocabularyConfig,
    label: str,
) -> tuple[dict[str, str], set[str]]:
    made: dict[str, str] = {}  # the source's identifier -> group ID
    touched: set[str] = set()
    with_parents: list[tuple[str, str, list[str]]] = []
    for number, row in enumerate(rows):
        where = label.format(number)
        if not row.get("name"):
            stats.skipped.append(f"{where}: no name")
            continue
        fields = {k: row[k] for k in store.GROUP_FIELDS if k in row}
        if "kind" in fields:
            fields["kind"] = str(fields["kind"]).lower()
        if "status" in fields:
            fields["status"] = str(fields["status"]).lower()
        external_id = str(row["id"]) if row.get("id") not in (None, "") else None
        try:
            with conn.begin_nested():
                group_id, action = store.upsert_group(
                    conn, fields, source=source, external_id=external_id, actor=actor,
                    vocabulary=vocabulary,
                )  # fmt: skip
        except GroupError as exc:
            stats.skipped.append(f"{where}: {exc}")
            continue
        stats.groups[action] += 1
        touched.add(group_id)
        if external_id:
            made[external_id] = group_id
        if action == "updated":
            stats.people |= store.current_members(conn, [group_id])
        if "parents" in row:
            with_parents.append((where, group_id, _parents(row["parents"])))
    for where, group_id, refs in with_parents:
        ids = []
        for ref in refs:
            found = _group_ref(conn, ref, source, made)
            if found:
                ids.append(found)
            else:
                stats.skipped.append(f"{where}: parent {ref!r} is not a known group")
        try:
            with conn.begin_nested():
                if store.set_parents(
                    conn, group_id, ids, actor=actor, source=source, only_source=True
                ):
                    stats.groups["nested"] += 1
        except GroupError as exc:
            stats.skipped.append(f"{where}: {exc}")
    return made, touched


def _import_memberships(
    conn: Connection,
    rows: list[dict[str, Any]],
    stats: ImportStats,
    *,
    source: str,
    actor: str,
    vocabulary: VocabularyConfig,
    made: dict[str, str],
    label: str,
) -> set[str]:
    touched: set[str] = set()
    by_external = {
        (r["qualifiers"] or {}).get("external_id"): r
        for r in store.memberships(conn, statuses=("accepted",))
        if (r["qualifiers"] or {}).get("source") == source
        and (r["qualifiers"] or {}).get("external_id")
    }
    for number, row in enumerate(rows):
        where = label.format(number)
        if not row.get("group"):
            stats.skipped.append(f"{where}: no group")
            continue
        group_id = _group_ref(conn, str(row["group"]), source, made)
        if group_id is None:
            stats.skipped.append(f"{where}: group {row['group']!r} is not a known group")
            continue
        person_id = _person(conn, row)
        if person_id is None:
            who = next((f"{k} {row[k]}" for k in ("person", "orcid", "email", "local_id")
                        if row.get(k)), "no person")  # fmt: skip
            stats.skipped.append(f"{where}: {who} is not in the directory (the roster adds people)")
            continue
        external_id = str(row["id"]) if row.get("id") not in (None, "") else None
        role = str(row.get("role") or "").lower() or None
        try:
            with conn.begin_nested():
                prior = by_external.get(external_id) if external_id else None
                if (
                    prior is not None
                    and prior["subject_id"] == person_id
                    and prior["object_id"] == group_id
                    and (role or vocabulary.default_role) != prior["qualifier_key"]
                ):
                    _change_role(conn, prior, role or vocabulary.default_role, vocabulary)
                membership_id, action = store.add_membership(
                    conn, person_id=person_id, group_id=group_id, vocabulary=vocabulary,
                    actor=actor, role=role, title=row.get("title"), start=row.get("start"),
                    end=row.get("end"), source=source, external_id=external_id,
                )  # fmt: skip
        except (GroupError, TesseraError) as exc:
            stats.skipped.append(f"{where}: {exc}")
            continue
        stats.memberships[action] += 1
        touched.add(membership_id)
        if action in ("created", "updated"):
            stats.people.add(person_id)
    return touched


def _change_role(
    conn: Connection, row: dict[str, Any], role: str, vocabulary: VocabularyConfig
) -> None:
    """A source's membership whose role changed: the same membership, in its new role."""
    store._role(role, vocabulary)
    clash = conn.execute(
        select(assertion.c.id).where(
            assertion.c.subject_id == row["subject_id"],
            assertion.c.predicate == "member_of",
            assertion.c.object_id == row["object_id"],
            assertion.c.qualifier_key == role,
        )
    ).scalar()
    if clash:
        return  # add_membership updates that one; the old role is ended by `complete`
    qualifiers = {**(row["qualifiers"] or {}), "role": role}
    conn.execute(
        update(assertion)
        .where(assertion.c.id == row["id"])
        .values(qualifier_key=role, qualifiers=qualifiers, updated_at=utc_now())
    )


def import_groups(
    engine: Engine,
    data: dict[str, list[dict[str, Any]]],
    *,
    source: str,
    vocabulary: VocabularyConfig,
    actor: str | None = None,
    complete: bool = False,
    dry_run: bool = False,
    today: date | None = None,
) -> ImportStats:
    """Import parsed rows (see ``read_file`` and ``parse_rows``) from one source."""
    source = store.check_source(source)
    if source == "manual":
        raise GroupError("'manual' is for groups made by hand; name the import's source.")
    actor = actor or f"{source} import"
    today = today or date.today()
    stats = ImportStats(source=source, dry_run=dry_run, complete=complete)
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            made, touched_groups = _import_groups(
                conn, data.get("groups", []), stats, source=source, actor=actor,
                vocabulary=vocabulary, label="group row {}",
            )  # fmt: skip
            touched_memberships = _import_memberships(
                conn, data.get("memberships", []), stats, source=source, actor=actor,
                vocabulary=vocabulary, made=made, label="membership row {}",
            )  # fmt: skip
            if complete:
                if data.get("groups"):
                    _archive_missing(conn, stats, source, touched_groups, actor, vocabulary)
                if data.get("memberships"):
                    _end_missing(conn, stats, source, touched_memberships, actor, today)
        except BaseException:
            transaction.rollback()
            raise
        if dry_run:
            transaction.rollback()
        else:
            transaction.commit()
    return stats


def _archive_missing(
    conn: Connection,
    stats: ImportStats,
    source: str,
    touched: set[str],
    actor: str,
    vocabulary: VocabularyConfig,
) -> None:
    for group_id in conn.execute(
        select(unit.c.id).where(unit.c.source == source, unit.c.status == "active")
    ).scalars():
        if group_id not in touched:
            store.update_group(
                conn, group_id, {"status": "archived"}, actor=actor, vocabulary=vocabulary
            )
            stats.groups["archived"] += 1
            stats.people |= store.current_members(conn, [group_id])


def _end_missing(
    conn: Connection,
    stats: ImportStats,
    source: str,
    touched: set[str],
    actor: str,
    today: date,
) -> None:
    for row in store.memberships(conn):
        q = row["qualifiers"] or {}
        if q.get("source") != source or row["id"] in touched or not is_current(q, today):
            continue
        after = {**q, "end": today.isoformat()}
        conn.execute(
            update(assertion)
            .where(assertion.c.id == row["id"])
            .values(qualifiers=after, updated_at=utc_now())
        )
        _event(
            conn, row["object_id"], actor, "member_end", membership=row["id"],
            person_id=row["subject_id"], before=q, after=after,
        )  # fmt: skip
        stats.memberships["ended"] += 1
        stats.people.add(row["subject_id"])


__all__ = ["ImportStats", "import_groups", "parse_rows", "read_file"]
