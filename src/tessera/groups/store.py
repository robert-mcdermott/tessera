"""Groups and memberships in the ledger.

* A **group** is a row of ``unit`` (the table keeps its first name). Its address (``slug``) is
  set when it is created and does not follow later renames, so links to it keep working.
* It may sit under **several parents** (``unit_parent``); nesting never makes a loop.
* It records its **source** (``manual``, ``roster``, or a feed's name) and its identifier
  there (``external_id``), so a later import from that source updates it.
* **Archived** groups keep their history and memberships but leave lists and pickers.
  **Merged** groups point to the group that absorbed their memberships, links, and roles; an
  import that names a merged group's identifier updates the group it was merged into.
* A **membership** is a ``member_of`` link from a person to a group, with a role (also its
  ``qualifier_key``, so one person may hold two roles in one group), an optional title, dates
  as precise as known, and its own source and identifier. Ending a membership keeps it, so
  alumni stay visible as alumni; removing one rejects it, for memberships that were wrong.
* Every change is written to ``unit_event`` with the values before it.

Functions take a connection inside the caller's transaction.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from typing import Any

from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.engine import Connection

from tessera.config import VocabularyConfig
from tessera.core.clock import utc_now
from tessera.core.errors import TesseraError
from tessera.core.ids import new_id
from tessera.evidence.memberships import is_current, parse_partial_date
from tessera.ledger.tables import assertion, person, role_grant, unit, unit_event, unit_parent

GROUP_FIELDS = (
    "name", "kind", "slug", "short_name", "code", "description", "email", "website", "status",
)  # fmt: skip
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MEMBERSHIP_FIELDS = ("role", "title", "start", "end")
_SOURCE = re.compile(r"^[a-z][a-z0-9_.-]{0,39}$")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class GroupError(TesseraError):
    """A request about groups that cannot be done as asked."""


class GroupNotFound(GroupError):
    pass


def slugify(text: str) -> str:
    """``Division of Neuroscience`` -> ``division-of-neuroscience`` (ASCII, at most 80)."""
    ascii_ = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_).strip("-")[:80].strip("-") or "group"


def check_source(source: str) -> str:
    source = (source or "").strip().lower()
    if not _SOURCE.match(source):
        raise GroupError(
            f"{source!r} is not a source name (lowercase letters, digits, '.', '_', '-')"
        )
    return source


def _unique_slug(conn: Connection, base: str, exclude: str | None = None) -> str:
    taken = set(
        conn.execute(
            select(unit.c.slug).where(
                or_(unit.c.slug == base, unit.c.slug.like(f"{base}-%")),
                unit.c.id != (exclude or ""),
            )
        ).scalars()
    )
    slug, n = base, 1
    while slug in taken:
        n += 1
        slug = f"{base}-{n}"
    return slug


def _event(conn: Connection, group_id: str, actor: str, action: str, **detail: Any) -> None:
    conn.execute(
        insert(unit_event).values(
            unit_id=group_id, actor=actor, action=action, detail=detail, at=utc_now()
        )
    )


# --- reading ------------------------------------------------------------------------------


def get_group(conn: Connection, group_id: str) -> dict[str, Any]:
    row = conn.execute(select(unit).where(unit.c.id == group_id)).mappings().first()
    if row is None:
        raise GroupNotFound(f"No group {group_id!r}.")
    return dict(row)


def resolve(conn: Connection, ref: str, *, follow_merged: bool = True) -> dict[str, Any]:
    """A group by ID, address, or code; a merged group resolves to the one it became."""
    ref = (ref or "").strip()
    row = (
        conn.execute(
            select(unit).where(or_(unit.c.id == ref, unit.c.slug == ref, unit.c.code == ref))
        )
        .mappings()
        .first()
    )
    if row is None:
        raise GroupNotFound(f"No group {ref!r}.")
    seen = {row["id"]}
    while follow_merged and row["status"] == "merged" and row["merged_into"]:
        row = get_group(conn, row["merged_into"])
        if row["id"] in seen:  # pragma: no cover - merges never make loops
            break
        seen.add(row["id"])
    return dict(row)


def parents_of(conn: Connection, group_ids: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    ids = sorted(set(group_ids))
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not ids:
        return out
    rows = conn.execute(
        select(unit_parent.c.child_id, unit_parent.c.source, unit.c.id, unit.c.name, unit.c.kind)
        .join(unit, unit.c.id == unit_parent.c.parent_id)
        .where(unit_parent.c.child_id.in_(ids))
        .order_by(unit.c.name)
    ).mappings()
    for r in rows:
        out[r["child_id"]].append(
            {"id": r["id"], "name": r["name"], "kind": r["kind"], "source": r["source"]}
        )
    return out


def children_of(conn: Connection, group_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        select(unit.c.id, unit.c.name, unit.c.kind, unit.c.status, unit.c.slug)
        .join(unit_parent, unit_parent.c.child_id == unit.c.id)
        .where(unit_parent.c.parent_id == group_id, unit.c.status != "merged")
        .order_by(unit.c.name)
    ).mappings()
    return [dict(r) for r in rows]


def descendants(conn: Connection, group_ids: Iterable[str]) -> set[str]:
    """The groups given and every group beneath them, through any parent."""
    children: dict[str, list[str]] = defaultdict(list)
    for child, parent in conn.execute(select(unit_parent.c.child_id, unit_parent.c.parent_id)):
        children[parent].append(child)
    found = set(group_ids)
    stack = list(found)
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in found:
                found.add(child)
                stack.append(child)
    return found


def _ancestors(conn: Connection, group_id: str) -> set[str]:
    parents: dict[str, list[str]] = defaultdict(list)
    for child, parent in conn.execute(select(unit_parent.c.child_id, unit_parent.c.parent_id)):
        parents[child].append(parent)
    found: set[str] = set()
    stack = [group_id]
    while stack:
        for parent in parents.get(stack.pop(), []):
            if parent not in found:
                found.add(parent)
                stack.append(parent)
    return found


# --- groups -------------------------------------------------------------------------------


def _clean(
    fields: dict[str, Any], vocabulary: VocabularyConfig, *, partial: bool
) -> dict[str, Any]:
    """Validated group fields; ``partial`` for changes, where absent fields stay as they are."""
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if key not in GROUP_FIELDS:
            raise GroupError(f"Unknown group field {key!r}.")
        if isinstance(value, str):
            value = value.strip()
        if key == "name":
            if not value:
                raise GroupError("A group needs a name.")
            if len(value) > 300:
                raise GroupError("A group's name is at most 300 characters.")
        elif key == "kind":
            if value not in dict(vocabulary.kinds):
                kinds = ", ".join(k for k, _ in vocabulary.kinds)
                raise GroupError(
                    f"{value!r} is not a kind of group here ({kinds}); "
                    "add it to [vocabulary.kinds] first."
                )
        elif key == "slug":
            if (value and not _SLUG.match(value)) or len(value or "") > 120:
                raise GroupError(
                    "An address is lowercase letters and digits joined by single hyphens."
                )
        elif key == "status":
            if value not in ("active", "archived"):
                raise GroupError("A group's status is active or archived.")
        elif key in ("short_name", "code") and value and len(value) > 80:
            raise GroupError(f"A group's {key.replace('_', ' ')} is at most 80 characters.")
        elif key == "description" and value and len(value) > 4000:
            raise GroupError("A group's description is at most 4,000 characters.")
        elif key == "email" and value and (len(value) > 320 or not _EMAIL.match(value)):
            raise GroupError(f"{value!r} is not an email address.")
        elif (
            key == "website"
            and value
            and (len(value) > 500 or not re.match(r"^https?://[^\s/]+\.[^\s]+$", value))
        ):
            raise GroupError("A website is an address starting with https://.")
        optional = key in ("short_name", "code", "description", "slug", "email", "website")
        out[key] = (value or None) if optional else value
    if not partial and not out.get("name"):
        raise GroupError("A group needs a name.")
    return out


def _check_code(conn: Connection, code: str | None, exclude: str | None = None) -> None:
    if code:
        other = conn.execute(
            select(unit.c.name).where(unit.c.code == code, unit.c.id != (exclude or ""))
        ).scalar()
        if other:
            raise GroupError(f"The code {code!r} already belongs to {other}.")


def create_group(
    conn: Connection,
    fields: dict[str, Any],
    *,
    actor: str,
    vocabulary: VocabularyConfig,
    parents: Iterable[str] = (),
    source: str = "manual",
    external_id: str | None = None,
) -> str:
    values = _clean({"kind": vocabulary.default_kind, **fields}, vocabulary, partial=False)
    _check_code(conn, values.get("code"))
    if values.get("slug"):
        if conn.execute(select(unit.c.id).where(unit.c.slug == values["slug"])).scalar():
            raise GroupError(f"The address {values['slug']!r} is taken.")
    else:
        values["slug"] = _unique_slug(conn, slugify(values["name"]))
    now = utc_now()
    group_id = new_id("unt")
    conn.execute(
        insert(unit).values(
            id=group_id,
            status=values.pop("status", None) or "active",
            source=source,
            external_id=external_id or None,
            created_at=now,
            updated_at=now,
            **values,
        )
    )
    _event(
        conn,
        group_id,
        actor,
        "create",
        fields={k: v for k, v in values.items() if v is not None},
        source=source,
        external_id=external_id,
    )
    if parents:
        set_parents(conn, group_id, parents, actor=actor, source=source)
    return group_id


def update_group(
    conn: Connection,
    group_id: str,
    changes: dict[str, Any],
    *,
    actor: str,
    vocabulary: VocabularyConfig,
    action: str = "edit",
) -> dict[str, Any]:
    """Change a group's fields; returns {field: [before, after]} for what changed."""
    row = get_group(conn, group_id)
    if row["status"] == "merged":
        raise GroupError(f"{row['name']} was merged into another group; change that one.")
    values = _clean(changes, vocabulary, partial=True)
    if "slug" in values:
        if not values["slug"]:
            values.pop("slug")  # an address cannot be cleared
        elif conn.execute(
            select(unit.c.id).where(unit.c.slug == values["slug"], unit.c.id != group_id)
        ).scalar():
            raise GroupError(f"The address {values['slug']!r} is taken.")
    if "code" in values:
        _check_code(conn, values["code"], exclude=group_id)
    changed = {k: [row[k], v] for k, v in values.items() if row[k] != v}
    if not changed:
        return {}
    conn.execute(
        update(unit)
        .where(unit.c.id == group_id)
        .values(**{k: v[1] for k, v in changed.items()}, updated_at=utc_now())
    )
    if "status" in changed:
        action = "archive" if changed["status"][1] == "archived" else "restore"
    _event(conn, group_id, actor, action, changes=changed)
    return changed


def set_parents(
    conn: Connection,
    group_id: str,
    parent_ids: Iterable[str],
    *,
    actor: str,
    source: str = "manual",
    only_source: bool = False,
) -> bool:
    """Make these the group's parents. With ``only_source``, only links from ``source`` are
    replaced (an import), and links made by hand or by other sources stay."""
    wanted = list(dict.fromkeys(parent_ids))
    if group_id in wanted:
        raise GroupError("A group cannot be its own parent.")
    below = descendants(conn, [group_id])
    for pid in wanted:
        parent = get_group(conn, pid)
        if parent["status"] == "merged":
            raise GroupError(f"{parent['name']} was merged into another group.")
        if pid in below:
            raise GroupError(f"{parent['name']} is inside this group, so it cannot also hold it.")
    current = {
        r.parent_id: r.source
        for r in conn.execute(
            select(unit_parent.c.parent_id, unit_parent.c.source).where(
                unit_parent.c.child_id == group_id
            )
        )
    }
    removable = {p for p, s in current.items() if not only_source or s == source}
    drop = removable - set(wanted)
    add = [p for p in wanted if p not in current]
    if not (drop or add):
        return False
    if drop:
        conn.execute(
            delete(unit_parent).where(
                unit_parent.c.child_id == group_id, unit_parent.c.parent_id.in_(sorted(drop))
            )
        )
    now = utc_now()
    for pid in add:
        conn.execute(
            insert(unit_parent).values(
                child_id=group_id, parent_id=pid, source=source, created_at=now
            )
        )
    _event(
        conn,
        group_id,
        actor,
        "nest",
        parents_before=sorted(current),
        parents=sorted((set(current) - drop) | set(add)),
    )
    return True


def merge_groups(conn: Connection, from_id: str, into_id: str, *, actor: str) -> dict[str, int]:
    """Move one group's memberships, sub-groups, parents, and scoped roles to another, and
    mark it merged. Where a person holds the same role in both, the dates widen to cover
    both. A merge is recorded in both groups' history; it is not undone automatically."""
    if from_id == into_id:
        raise GroupError("A group cannot be merged into itself.")
    source, target = get_group(conn, from_id), get_group(conn, into_id)
    for row in (source, target):
        if row["status"] == "merged":
            raise GroupError(f"{row['name']} was already merged into another group.")
    if into_id in descendants(conn, [from_id]) - {from_id}:
        raise GroupError(f"{target['name']} is inside {source['name']}; merge the other way.")
    moved = {"memberships": 0, "combined": 0, "subgroups": 0, "parents": 0, "roles": 0}
    existing = {
        (r.subject_id, r.qualifier_key): r
        for r in conn.execute(
            select(assertion.c.id, assertion.c.subject_id, assertion.c.qualifier_key,
                   assertion.c.qualifiers, assertion.c.status).where(
                assertion.c.predicate == "member_of", assertion.c.object_id == into_id
            )
        )
    }  # fmt: skip
    now = utc_now()
    for r in conn.execute(
        select(assertion.c.id, assertion.c.subject_id, assertion.c.qualifier_key,
               assertion.c.qualifiers, assertion.c.status).where(
            assertion.c.predicate == "member_of", assertion.c.object_id == from_id
        )
    ).all():  # fmt: skip
        other = existing.get((r.subject_id, r.qualifier_key))
        if other is None:
            conn.execute(
                update(assertion)
                .where(assertion.c.id == r.id)
                .values(object_id=into_id, updated_at=now)
            )
            moved["memberships"] += 1
            continue
        if r.status == "accepted" and other.status == "accepted":
            widened = _widen(other.qualifiers or {}, r.qualifiers or {})
            if widened != (other.qualifiers or {}):
                conn.execute(
                    update(assertion)
                    .where(assertion.c.id == other.id)
                    .values(qualifiers=widened, updated_at=now)
                )
        conn.execute(delete(assertion).where(assertion.c.id == r.id))
        moved["combined"] += 1
    # Sub-groups move under the target; the source's own parents become the target's.
    for child, link_source in conn.execute(
        select(unit_parent.c.child_id, unit_parent.c.source).where(
            unit_parent.c.parent_id == from_id
        )
    ).all():
        conn.execute(
            delete(unit_parent).where(
                unit_parent.c.child_id == child, unit_parent.c.parent_id == from_id
            )
        )
        if child == into_id:
            continue
        held = conn.execute(
            select(func.count()).where(
                unit_parent.c.child_id == child, unit_parent.c.parent_id == into_id
            )
        ).scalar()
        if not held:
            conn.execute(
                insert(unit_parent).values(
                    child_id=child, parent_id=into_id, source=link_source, created_at=now
                )
            )
            moved["subgroups"] += 1
    above_target = _ancestors(conn, into_id) | {into_id}
    below_target = descendants(conn, [into_id])
    for parent, link_source in conn.execute(
        select(unit_parent.c.parent_id, unit_parent.c.source).where(
            unit_parent.c.child_id == from_id
        )
    ).all():
        conn.execute(
            delete(unit_parent).where(
                unit_parent.c.child_id == from_id, unit_parent.c.parent_id == parent
            )
        )
        if parent in above_target or parent in below_target:
            continue
        conn.execute(
            insert(unit_parent).values(
                child_id=into_id, parent_id=parent, source=link_source, created_at=now
            )
        )
        moved["parents"] += 1
    moved["roles"] = (
        conn.execute(
            update(role_grant)
            .where(role_grant.c.scope_type == "unit", role_grant.c.scope_id == from_id)
            .values(scope_id=into_id)
        ).rowcount
        or 0
    )
    conn.execute(
        update(unit)
        .where(unit.c.id == from_id)
        .values(status="merged", merged_into=into_id, updated_at=now)
    )
    _event(conn, from_id, actor, "merge", into=into_id, into_name=target["name"], **moved)
    _event(conn, into_id, actor, "absorb", merged=from_id, merged_name=source["name"], **moved)
    return moved


def _widen(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Dates covering both memberships: the earlier start, the later end (none if either is
    open). Other qualifiers come from ``a``."""
    out = dict(a)
    out.pop("start", None)
    out.pop("end", None)
    if a.get("start") and b.get("start"):
        out["start"] = min(a["start"], b["start"])
    if a.get("end") and b.get("end"):
        out["end"] = max(a["end"], b["end"])
    if not out.get("title") and b.get("title"):
        out["title"] = b["title"]
    return out


def find_for_import(
    conn: Connection,
    *,
    source: str,
    external_id: str | None,
    code: str | None,
    name: str | None,
) -> dict[str, Any] | None:
    """The group an imported row describes: by its identifier in this source (following
    merges), then by code, then by name among groups that have no identifier anywhere."""
    if external_id:
        found = (
            conn.execute(
                select(unit).where(unit.c.source == source, unit.c.external_id == external_id)
            )
            .mappings()
            .first()
        )
        if found:
            return resolve(conn, found["id"])
    if code:
        found = conn.execute(select(unit).where(unit.c.code == code)).mappings().first()
        if found:
            return resolve(conn, found["id"])
    if name:  # only groups no source has given an identifier yet
        rows = (
            conn.execute(
                select(unit).where(
                    unit.c.name == name, unit.c.status != "merged", unit.c.external_id.is_(None)
                )
            )
            .mappings()
            .all()
        )
        rows = sorted(rows, key=lambda r: (r["source"] != source, r["created_at"]))
        if rows:
            return dict(rows[0])
    return None


def upsert_group(
    conn: Connection,
    fields: dict[str, Any],
    *,
    source: str,
    external_id: str | None,
    actor: str,
    vocabulary: VocabularyConfig,
) -> tuple[str, str]:
    """Create or update the group an import row describes; returns (ID, created|updated|
    unchanged). Only the fields the row gives change."""
    found = find_for_import(
        conn,
        source=source,
        external_id=external_id,
        code=fields.get("code"),
        name=fields.get("name"),
    )
    if found is None:
        group_id = create_group(
            conn, fields, actor=actor, vocabulary=vocabulary, source=source,
            external_id=external_id,
        )  # fmt: skip
        return group_id, "created"
    changes = {k: v for k, v in fields.items() if k != "slug"}
    held = (
        external_id
        and conn.execute(
            select(unit.c.id).where(unit.c.source == source, unit.c.external_id == external_id)
        ).scalar()
    )
    if (
        external_id
        and not held  # found through a merge: the identifier stays with the merged group
        and not found["external_id"]
        and found["source"] in (source, "manual")
    ):
        # A group made by hand (or before its feed had identifiers) is adopted by the feed.
        conn.execute(
            update(unit)
            .where(unit.c.id == found["id"])
            .values(source=source, external_id=external_id, updated_at=utc_now())
        )
    changed = update_group(
        conn, found["id"], changes, actor=actor, vocabulary=vocabulary, action="import"
    )
    return found["id"], "updated" if changed else "unchanged"


# --- memberships --------------------------------------------------------------------------


def membership_view(
    row: dict[str, Any], vocabulary: VocabularyConfig, today: date | None = None
) -> dict[str, Any]:
    q = row.get("qualifiers") or {}
    role = q.get("role") or row.get("qualifier_key") or vocabulary.default_role
    return {
        "id": row["id"],
        "person_id": row["subject_id"],
        "group_id": row["object_id"],
        "role": role,
        "role_label": vocabulary.role_label(role),
        "leader": role in vocabulary.leader_roles,
        "title": q.get("title"),
        "start": q.get("start"),
        "end": q.get("end"),
        "current": is_current(q, today or date.today()),
        "source": q.get("source") or "manual",
        "external_id": q.get("external_id"),
        "status": row["status"],
        "decided_by": row.get("decided_by"),
        "decided_at": row.get("decided_at"),
    }


def memberships(
    conn: Connection,
    *,
    group_ids: Iterable[str] | None = None,
    person_ids: Iterable[str] | None = None,
    statuses: tuple[str, ...] = ("accepted",),
) -> list[dict[str, Any]]:
    query = select(assertion).where(
        assertion.c.predicate == "member_of", assertion.c.status.in_(statuses)
    )
    if group_ids is not None:
        query = query.where(assertion.c.object_id.in_(sorted(set(group_ids))))
    if person_ids is not None:
        query = query.where(assertion.c.subject_id.in_(sorted(set(person_ids))))
    return [dict(r) for r in conn.execute(query).mappings()]


def get_membership(conn: Connection, membership_id: str) -> dict[str, Any]:
    row = (
        conn.execute(
            select(assertion).where(
                assertion.c.id == membership_id, assertion.c.predicate == "member_of"
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise GroupNotFound(f"No membership {membership_id!r}.")
    return dict(row)


def _dates(start: Any, end: Any) -> tuple[str | None, str | None]:
    start = parse_partial_date(str(start)) if start else None
    end = parse_partial_date(str(end)) if end else None
    if start and end and start > end:
        raise GroupError(f"The membership ends ({end}) before it starts ({start}).")
    return start, end


def _role(role: str | None, vocabulary: VocabularyConfig) -> str:
    role = (role or "").strip() or vocabulary.default_role
    if role not in dict(vocabulary.roles):
        roles = ", ".join(k for k, _ in vocabulary.roles)
        raise GroupError(f"{role!r} is not a membership role here ({roles}).")
    return role


def add_membership(
    conn: Connection,
    *,
    person_id: str,
    group_id: str,
    vocabulary: VocabularyConfig,
    actor: str,
    role: str | None = None,
    title: str | None = None,
    start: Any = None,
    end: Any = None,
    source: str = "manual",
    external_id: str | None = None,
) -> tuple[str, str]:
    """Add a membership, or bring one with the same person, group, and role up to date.

    Returns (ID, created|updated|unchanged|kept). A membership someone removed by hand stays
    removed when a source lists it again (``kept``)."""
    role = _role(role, vocabulary)
    start, end = _dates(start, end)
    group = get_group(conn, group_id)
    if group["status"] == "merged":
        raise GroupError(f"{group['name']} was merged into another group.")
    who = conn.execute(select(person.c.status).where(person.c.id == person_id)).scalar()
    if who is None:
        raise GroupNotFound(f"No person {person_id!r}.")
    if who == "merged":
        raise GroupError("That person's record was merged into another; use that one.")
    qualifiers = {
        k: v
        for k, v in (
            ("role", role),
            ("title", (title or "").strip() or None),
            ("start", start),
            ("end", end),
            ("source", source),
            ("external_id", external_id or None),
        )
        if v
    }
    now = utc_now()
    existing = (
        conn.execute(
            select(assertion).where(
                assertion.c.subject_id == person_id,
                assertion.c.predicate == "member_of",
                assertion.c.object_id == group_id,
                assertion.c.qualifier_key == role,
            )
        )
        .mappings()
        .first()
    )
    if existing is not None:
        if existing["status"] == "rejected" and source != "manual":
            return existing["id"], "kept"
        before = existing["qualifiers"] or {}
        if before == qualifiers and existing["status"] == "accepted":
            return existing["id"], "unchanged"
        conn.execute(
            update(assertion)
            .where(assertion.c.id == existing["id"])
            .values(
                qualifiers=qualifiers,
                status="accepted",
                reported=True,
                last_evaluated_at=now,
                updated_at=now,
                **({"decided_by": actor, "decided_at": now} if source == "manual" else {}),
            )
        )
        _event(
            conn, group_id, actor, "member_edit", membership=existing["id"],
            person_id=person_id, before=before, after=qualifiers,
        )  # fmt: skip
        return existing["id"], "updated"
    membership_id = new_id("asr")
    manual = source == "manual"
    conn.execute(
        insert(assertion).values(
            id=membership_id,
            subject_type="person",
            subject_id=person_id,
            predicate="member_of",
            object_type="unit",
            object_id=group_id,
            qualifier_key=role,
            qualifiers=qualifiers,
            origin="human" if manual else "source",
            method="manual" if manual else f"{source}/v1"[:60],
            score=None,
            evidence=[
                {
                    "feature": "manual" if manual else source,
                    "value": external_id,
                    "weight": None,
                    "explanation": f"Added by {actor}" if manual else f"Listed by {source}",
                    "source_ref": source,
                }
            ],
            status="accepted",
            locked=manual,
            reported=True,
            decided_by=actor if manual else None,
            decided_at=now if manual else None,
            first_seen_at=now,
            last_evaluated_at=now,
            updated_at=now,
        )
    )
    _event(
        conn, group_id, actor, "member_add", membership=membership_id, person_id=person_id,
        after=qualifiers,
    )  # fmt: skip
    return membership_id, "created"


def update_membership(
    conn: Connection,
    membership_id: str,
    changes: dict[str, Any],
    *,
    actor: str,
    vocabulary: VocabularyConfig,
) -> dict[str, Any]:
    """Change a membership's role, title, or dates (setting ``end`` ends it)."""
    row = get_membership(conn, membership_id)
    if row["status"] == "rejected":
        raise GroupError("That membership was removed.")
    unknown = set(changes) - set(MEMBERSHIP_FIELDS)
    if unknown:
        raise GroupError(f"Unknown membership field {sorted(unknown)[0]!r}.")
    before = dict(row["qualifiers"] or {})
    after = dict(before)
    if "role" in changes:
        after["role"] = _role(changes["role"], vocabulary)
    if "title" in changes:
        after["title"] = (changes["title"] or "").strip() or None
    start = changes.get("start", before.get("start"))
    end = changes.get("end", before.get("end"))
    after["start"], after["end"] = _dates(start, end)
    after = {k: v for k, v in after.items() if v}
    if after == before:
        return {}
    role = after.get("role") or vocabulary.default_role
    if role != row["qualifier_key"]:
        clash = conn.execute(
            select(assertion.c.id).where(
                assertion.c.subject_id == row["subject_id"],
                assertion.c.predicate == "member_of",
                assertion.c.object_id == row["object_id"],
                assertion.c.qualifier_key == role,
                assertion.c.status != "rejected",
            )
        ).scalar()
        if clash:
            raise GroupError("This person already holds that role in this group; change that one.")
        conn.execute(  # a removed membership in that role gives way to this one
            delete(assertion).where(
                assertion.c.subject_id == row["subject_id"],
                assertion.c.predicate == "member_of",
                assertion.c.object_id == row["object_id"],
                assertion.c.qualifier_key == role,
            )
        )
    now = utc_now()
    conn.execute(
        update(assertion)
        .where(assertion.c.id == membership_id)
        .values(
            qualifier_key=role,
            qualifiers=after,
            locked=True,
            decided_by=actor,
            decided_at=now,
            updated_at=now,
        )
    )
    action = "member_end" if after.get("end") and not before.get("end") else "member_edit"
    _event(
        conn, row["object_id"], actor, action, membership=membership_id,
        person_id=row["subject_id"], before=before, after=after,
    )  # fmt: skip
    return {"before": before, "after": after}


def remove_membership(
    conn: Connection, membership_id: str, *, actor: str, note: str | None = None
) -> None:
    """Reject a membership that was wrong. To record that someone left, end it instead."""
    row = get_membership(conn, membership_id)
    if row["status"] == "rejected":
        return
    now = utc_now()
    conn.execute(
        update(assertion)
        .where(assertion.c.id == membership_id)
        .values(
            status="rejected",
            locked=True,
            decided_by=actor,
            decided_at=now,
            decision_note=note,
            updated_at=now,
        )
    )
    _event(
        conn, row["object_id"], actor, "member_remove", membership=membership_id,
        person_id=row["subject_id"], before=row["qualifiers"] or {}, note=note,
    )  # fmt: skip


def current_members(
    conn: Connection, group_ids: Iterable[str], today: date | None = None
) -> set[str]:
    today = today or date.today()
    return {
        r["subject_id"]
        for r in memberships(conn, group_ids=group_ids)
        if is_current(r["qualifiers"], today)
    }


def member_counts(
    conn: Connection, today: date | None = None, group_ids: Iterable[str] | None = None
) -> dict[str, dict[str, int]]:
    """{group ID: {"current": n, "alumni": n}}, counting people once per group: every group's,
    or only these groups'. Only what counting needs is read (never the evidence)."""
    today = today or date.today()
    current: dict[str, set[str]] = defaultdict(set)
    past: dict[str, set[str]] = defaultdict(set)
    query = select(assertion.c.subject_id, assertion.c.object_id, assertion.c.qualifiers).where(
        assertion.c.predicate == "member_of", assertion.c.status == "accepted"
    )
    if group_ids is not None:
        query = query.where(assertion.c.object_id.in_(sorted(set(group_ids))))
    for subject_id, group_id, qualifiers in conn.execute(query):
        (current if is_current(qualifiers, today) else past)[group_id].add(subject_id)
    return {
        gid: {"current": len(current[gid]), "alumni": len(past[gid] - current[gid])}
        for gid in set(current) | set(past)
    }


def events(conn: Connection, group_id: str, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn.execute(
        select(unit_event)
        .where(unit_event.c.unit_id == group_id)
        .order_by(unit_event.c.id.desc())
        .limit(limit)
    ).mappings()
    return [dict(r) for r in rows]


__all__ = [
    "GroupError",
    "GroupNotFound",
    "add_membership",
    "check_source",
    "children_of",
    "create_group",
    "current_members",
    "descendants",
    "events",
    "find_for_import",
    "get_group",
    "get_membership",
    "member_counts",
    "membership_view",
    "memberships",
    "merge_groups",
    "parents_of",
    "remove_membership",
    "resolve",
    "set_parents",
    "slugify",
    "update_group",
    "update_membership",
    "upsert_group",
]
