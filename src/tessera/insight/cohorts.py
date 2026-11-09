"""Cohorts: whose work a figure counts.

A cohort is written as a short specification, in the address and in saved cohorts:

* ``organization``: everyone in the directory, current and former;
* ``group:<address or ID>``, optionally with ``+below`` for the groups beneath it: the
  group's members. By default each member's work counts from the year they joined to the year
  they left, where the dates are known (``window = membership``). ``window = current`` counts
  everything the current members have done instead;
* ``people:<ID>,<ID>,…``: picked people;
* ``search:<words>``: the people a directory search finds;
* ``coh_…``: a saved cohort, which holds one of the above under a name.

Resolving a cohort gives its members, each with the years their work counts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pyarrow as pa
from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Connection, Engine

from tessera.core.clock import utc_now
from tessera.core.errors import TesseraError
from tessera.core.ids import new_id
from tessera.evidence.memberships import is_current
from tessera.groups import store as groups
from tessera.ledger.tables import cohort as cohort_table
from tessera.ledger.tables import person

KINDS = ("organization", "group", "people", "search")
WINDOWS = ("membership", "current")
MAX_PEOPLE = 2000
DIRECTORY = ("active", "former")


class CohortError(TesseraError):
    """A cohort that cannot be resolved as written."""


@dataclass(frozen=True)
class CohortSpec:
    kind: str = "organization"
    group: str | None = None  # a group's address, code, name, or ID
    below: bool = False  # include the groups beneath it
    window: str = "membership"  # membership: while members; current: current members' all
    people: tuple[str, ...] = ()
    q: str = ""

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind}
        if self.kind == "group":
            out |= {"group": self.group, "below": self.below, "window": self.window}
        elif self.kind == "people":
            out["people"] = list(self.people)
        elif self.kind == "search":
            out["q"] = self.q
        return out

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CohortSpec:
        kind = data.get("kind", "organization")
        if kind not in KINDS:
            raise CohortError(f"A cohort is one of {', '.join(KINDS)}.")
        window = data.get("window") or "membership"
        if window not in WINDOWS:
            raise CohortError("A group cohort counts work while members, or current members'.")
        people = tuple(str(p) for p in data.get("people") or ())
        if kind == "people" and not people:
            raise CohortError("Pick at least one person.")
        if len(people) > MAX_PEOPLE:
            raise CohortError(f"A cohort has at most {MAX_PEOPLE:,} picked people.")
        if kind == "group" and not data.get("group"):
            raise CohortError("Name the group.")
        if kind == "search" and not str(data.get("q") or "").strip():
            raise CohortError("A search cohort needs words to search for.")
        return cls(
            kind=kind,
            group=str(data["group"]) if data.get("group") else None,
            below=bool(data.get("below")),
            window=window,
            people=people,
            q=str(data.get("q") or "").strip()[:200],
        )

    def text(self) -> str:
        """The specification as written in an address."""
        if self.kind == "group":
            text = f"group:{self.group}{'+below' if self.below else ''}"
            return text if self.window == "membership" else f"{text};current"
        if self.kind == "people":
            return "people:" + ",".join(self.people)
        if self.kind == "search":
            return f"search:{self.q}"
        return "organization"


def parse(text: str | None) -> CohortSpec | str:
    """A specification, or a saved cohort's ID (``coh_…``) to look up."""
    text = (text or "").strip()
    if not text or text == "organization":
        return CohortSpec()
    if text.startswith("coh_"):
        return text
    kind, _, value = text.partition(":")
    value = value.strip()
    if kind == "group":
        window = "membership"
        if value.endswith(";current"):
            value, window = value.removesuffix(";current"), "current"
        below = value.endswith("+below")
        return CohortSpec.from_json(
            {"kind": "group", "group": value.removesuffix("+below").strip(), "below": below,
             "window": window}
        )  # fmt: skip
    if kind == "people":
        return CohortSpec.from_json(
            {"kind": "people", "people": [p.strip() for p in value.split(",") if p.strip()]}
        )
    if kind == "search":
        return CohortSpec.from_json({"kind": "search", "q": value})
    raise CohortError(
        "A cohort is organization, group:<address>[+below], people:<ID>,<ID>, search:<words>, "
        "or a saved cohort's ID."
    )


@dataclass
class Member:
    person_id: str
    name: str
    start_year: int | None = None  # the first year their work counts (None: no limit)
    end_year: int | None = None  # the last year (None: no limit)

    def counts(self, year: int | None) -> bool:
        if year is None:
            return self.start_year is None and self.end_year is None
        return (self.start_year is None or year >= self.start_year) and (
            self.end_year is None or year <= self.end_year
        )


@dataclass
class Cohort:
    spec: CohortSpec
    label: str
    members: dict[str, Member]
    note: str = ""
    saved: dict[str, Any] | None = None  # {"id", "name"} for a saved cohort
    group: dict[str, Any] | None = None  # {"id", "name", "slug", "kind"} for a group cohort
    dated: int = 0  # members whose dates limit what counts
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def organization(self) -> bool:
        return self.spec.kind == "organization"

    def to_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "kind": self.spec.kind,
            "spec": self.saved["id"] if self.saved else self.spec.text(),
            "definition": self.spec.to_json(),
            "size": self.size,
            "dated": self.dated,
            "note": self.note,
            "saved": self.saved,
            "group": self.group,
        }

    def table(self) -> pa.Table:
        rows = sorted(self.members.values(), key=lambda m: m.person_id)
        return pa.table(
            {
                "person_id": [m.person_id for m in rows],
                "name": [m.name for m in rows],
                "start_year": [m.start_year for m in rows],
                "end_year": [m.end_year for m in rows],
            },
            schema=MEMBERS_SCHEMA,
        )


MEMBERS_SCHEMA = pa.schema(
    [("person_id", pa.string()), ("name", pa.string()), ("start_year", pa.int32()),
     ("end_year", pa.int32())]
)  # fmt: skip


def members_from_table(table: pa.Table) -> dict[str, Member]:
    return {
        r["person_id"]: Member(r["person_id"], r["name"], r["start_year"], r["end_year"])
        for r in table.to_pylist()
    }


def _year(text: Any) -> int | None:
    if not text:
        return None
    try:
        return int(str(text)[:4])
    except ValueError:
        return None


def _directory(conn: Connection, ids: set[str] | None = None) -> dict[str, str]:
    query = select(person.c.id, person.c.display_name).where(person.c.status.in_(DIRECTORY))
    if ids is not None:
        if not ids:
            return {}
        query = query.where(person.c.id.in_(sorted(ids)))
    return {r.id: r.display_name for r in conn.execute(query)}


def resolve(
    engine: Engine,
    target: CohortSpec | str | None,
    *,
    today: date | None = None,
    search: Any = None,
) -> Cohort:
    """The cohort's members. ``search`` is the search index, for search cohorts."""
    today = today or date.today()
    with engine.connect() as conn:
        saved = None
        if target is None:
            spec = CohortSpec()
        elif isinstance(target, str):
            found = parse(target)
            if isinstance(found, str):
                saved = get_cohort(conn, found)
                spec = CohortSpec.from_json(saved["definition"])
            else:
                spec = found
        else:
            spec = target
        cohort = _resolve(conn, spec, today, search)
    if saved:
        cohort.saved = {"id": saved["id"], "name": saved["name"]}
        cohort.label = saved["name"]
    return cohort


def _resolve(conn: Connection, spec: CohortSpec, today: date, search: Any) -> Cohort:
    if spec.kind == "organization":
        names = _directory(conn)
        return Cohort(
            spec,
            "The organization",
            {pid: Member(pid, name) for pid, name in names.items()},
            note="Everyone in the directory, current and former.",
        )
    if spec.kind == "people":
        names = _directory(conn, set(spec.people))
        missing = [p for p in spec.people if p not in names]
        if missing and not names:
            raise CohortError(f"No person {missing[0]!r} in the directory.")
        label = names[spec.people[0]] if len(names) == 1 else f"{len(names)} people"
        note = "Picked people." + (f" {len(missing)} are no longer in the directory." if missing
                                    else "")  # fmt: skip
        return Cohort(spec, label, {p: Member(p, n) for p, n in names.items()}, note=note)
    if spec.kind == "search":
        if search is None:
            raise CohortError("A search cohort needs the search index.")
        ids: set[str] = set()
        offset = 0
        while len(ids) < MAX_PEOPLE:
            result = search.search("person", spec.q, None, None, 200, offset, with_facets=False)
            ids |= {item["ref"] for item in result.items}
            offset += 200
            if offset >= result.total:
                break
        names = _directory(conn, ids)
        return Cohort(
            spec, f"People matching “{spec.q}”", {p: Member(p, n) for p, n in names.items()},
            note="The people a directory search finds, as of now.",
        )  # fmt: skip
    # a group
    try:
        group = groups.resolve(conn, spec.group or "")
    except TesseraError as exc:
        raise CohortError(str(exc)) from exc
    ids_ = {group["id"]}
    if spec.below:
        ids_ |= groups.descendants(conn, ids_)
    rows = groups.memberships(conn, group_ids=ids_)
    windows: dict[str, list[tuple[int | None, int | None]]] = {}
    for r in rows:
        q = r["qualifiers"] or {}
        if spec.window == "current" and not is_current(q, today):
            continue
        windows.setdefault(r["subject_id"], []).append((_year(q.get("start")), _year(q.get("end"))))
    names = _directory(conn, set(windows))
    members: dict[str, Member] = {}
    dated = 0
    for pid, name in names.items():
        if spec.window == "current":
            members[pid] = Member(pid, name)
            continue
        spans = windows[pid]
        start = None if any(s is None for s, _ in spans) else min(s for s, _ in spans)  # type: ignore[type-var]
        end = None if any(e is None for _, e in spans) else max(e for _, e in spans)  # type: ignore[type-var]
        members[pid] = Member(pid, name, start, end)
        dated += start is not None or end is not None
    label = group["name"] + (" and the groups beneath it" if spec.below else "")
    if spec.window == "current":
        note = "Everything the group's current members have done, whenever they did it."
    else:
        note = (
            "Current and former members. Each member's work counts from the year they joined to "
            f"the year they left, where those dates are known ({dated} of {len(members)})."
        )
    return Cohort(
        spec, label, members, note=note, dated=dated,
        group={"id": group["id"], "name": group["name"], "slug": group["slug"],
               "kind": group["kind"]},
    )  # fmt: skip


# --- saved cohorts ---------------------------------------------------------------------------


def get_cohort(conn: Connection, cohort_id: str) -> dict[str, Any]:
    row = conn.execute(select(cohort_table).where(cohort_table.c.id == cohort_id)).mappings()
    found = row.first()
    if found is None:
        raise CohortError(f"No saved cohort {cohort_id!r}.")
    return dict(found)


def list_cohorts(conn: Connection) -> list[dict[str, Any]]:
    rows = conn.execute(select(cohort_table).order_by(cohort_table.c.name)).mappings()
    return [dict(r) for r in rows]


def _name(name: str) -> str:
    name = (name or "").strip()
    if not name or len(name) > 200:
        raise CohortError("A cohort's name has 1 to 200 characters.")
    return name


def save_cohort(
    conn: Connection, name: str, spec: CohortSpec, *, actor: str, description: str | None = None,
    owner: str | None = None,
) -> str:  # fmt: skip
    if (
        conn.execute(select(cohort_table.c.id).where(cohort_table.c.name == _name(name))).first()
        is not None
    ):
        raise CohortError(f"There is already a cohort called {name.strip()!r}.")
    cohort_id = new_id("coh")
    now = utc_now()
    conn.execute(
        insert(cohort_table).values(
            id=cohort_id, name=_name(name), description=(description or "").strip() or None,
            definition=spec.to_json(), created_by=actor, created_at=now, updated_by=actor,
            updated_at=now, owner=owner,
        )
    )  # fmt: skip
    return cohort_id


def update_cohort(
    conn: Connection,
    cohort_id: str,
    *,
    actor: str,
    name: str | None = None,
    spec: CohortSpec | None = None,
    description: str | None = None,
) -> None:
    get_cohort(conn, cohort_id)
    values: dict[str, Any] = {"updated_by": actor, "updated_at": utc_now()}
    if name is not None:
        values["name"] = _name(name)
        clash = conn.execute(
            select(cohort_table.c.id).where(
                cohort_table.c.name == values["name"], cohort_table.c.id != cohort_id
            )
        ).first()
        if clash is not None:
            raise CohortError(f"There is already a cohort called {values['name']!r}.")
    if spec is not None:
        values["definition"] = spec.to_json()
    if description is not None:
        values["description"] = description.strip() or None
    conn.execute(update(cohort_table).where(cohort_table.c.id == cohort_id).values(**values))


def delete_cohort(conn: Connection, cohort_id: str) -> None:
    get_cohort(conn, cohort_id)
    conn.execute(delete(cohort_table).where(cohort_table.c.id == cohort_id))


__all__ = [
    "MEMBERS_SCHEMA",
    "Cohort",
    "CohortError",
    "CohortSpec",
    "Member",
    "delete_cohort",
    "get_cohort",
    "list_cohorts",
    "members_from_table",
    "parse",
    "resolve",
    "save_cohort",
    "update_cohort",
]
