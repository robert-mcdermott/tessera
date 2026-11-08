"""A group's showcase (LAB-01, LAB-02): its leaders and people, the publications attributed to
it, its members' grants and studies, its topics and broader areas, and the groups it works
with.

Topics and areas are its current members' concept profiles and research areas, summed, so a
group's topics are what its people study now. Grants and studies are its current members'.
Publications are only those attributed to the group (``attributed_to``, decided by a person);
a member's paper is not the group's until someone says so.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from typing import Any

import pyarrow.parquet as pq
from sqlalchemy import select
from sqlalchemy.engine import Connection

from tessera.analytics.concepts import profile_path
from tessera.analytics.vocabulary import area_path
from tessera.config import Config
from tessera.evidence.memberships import is_current
from tessera.groups import store
from tessera.home import DataHome
from tessera.ledger.tables import assertion, person, unit

TOPICS_SHOWN = 12
AREAS_SHOWN = 6
AREA_FLOOR = 0.03  # smaller shares say little about a group
CONNECTIONS_SHOWN = 8


def people_of(
    conn: Connection, group_id: str, config: Config, today: date
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(current, alumni) membership views with names, leaders first, then by role and name."""
    rows = store.memberships(conn, group_ids=[group_id])
    views = [store.membership_view(r, config.vocabulary, today) for r in rows]
    ids = sorted({v["person_id"] for v in views})
    who = {
        r.id: r
        for r in conn.execute(
            select(person.c.id, person.c.display_name, person.c.family_name, person.c.status)
            .where(person.c.id.in_(ids))
        )
    }  # fmt: skip
    roles = {k: i for i, (k, _) in enumerate(config.vocabulary.roles)}
    for v in views:
        found = who.get(v["person_id"])
        v["name"] = found.display_name if found else v["person_id"]
        v["person_status"] = found.status if found else None
        v["_key"] = ((found.family_name if found else None) or v["name"]).lower()
    views = [v for v in views if v["person_status"] in ("active", "former")]
    views.sort(key=lambda v: (not v["leader"], roles.get(v["role"], len(roles)), v["_key"]))
    current = [v for v in views if v["current"]]
    here = {v["person_id"] for v in current}
    alumni: list[dict[str, Any]] = []
    seen: set[str] = set()
    for v in sorted(views, key=lambda v: (v["end"] or "") , reverse=True):  # fmt: skip
        if not v["current"] and v["person_id"] not in here and v["person_id"] not in seen:
            seen.add(v["person_id"])
            alumni.append(v)
    for v in [*current, *alumni]:
        v.pop("_key", None)
    return current, alumni


def topics_and_areas(
    home: DataHome, people: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The group's topics (its people's profiles, summed) and broader research areas."""
    wanted = set(people)
    topics: list[dict[str, Any]] = []
    areas: list[dict[str, Any]] = []
    if not wanted:
        return topics, areas
    path = profile_path(home)
    if path.is_file():
        weight: dict[str, float] = defaultdict(float)
        holders: dict[str, set[str]] = defaultdict(set)
        names: dict[str, str] = {}
        for r in pq.read_table(
            path, columns=["person_id", "descriptor_ui", "descriptor", "weight"]
        ).to_pylist():
            if r["person_id"] in wanted:
                weight[r["descriptor_ui"]] += r["weight"]
                holders[r["descriptor_ui"]].add(r["person_id"])
                names[r["descriptor_ui"]] = r["descriptor"]
        total = sum(weight.values()) or 1.0
        for ui, w in sorted(weight.items(), key=lambda kv: (-kv[1], names[kv[0]]))[:TOPICS_SHOWN]:
            topics.append(
                {
                    "descriptor_ui": ui,
                    "descriptor": names[ui],
                    "share": round(w / total, 4),
                    "people": len(holders[ui]),
                }
            )
    path = area_path(home)
    if path.is_file():
        share: dict[str, float] = defaultdict(float)
        info: dict[str, dict[str, Any]] = {}
        for r in pq.read_table(path).to_pylist():
            if r["person_id"] in wanted:
                share[r["area_tree"]] += r["share"]
                info[r["area_tree"]] = r
        total = sum(share.values()) or 1.0
        for tree, s in sorted(share.items(), key=lambda kv: -kv[1])[:AREAS_SHOWN]:
            if s / total < AREA_FLOOR:
                break
            areas.append(
                {
                    "area": info[tree]["area"],
                    "area_ui": info[tree]["area_ui"],
                    "category": info[tree]["category"],
                    "share": round(s / total, 4),
                }
            )
    return topics, areas


def linked(
    conn: Connection, people: list[str], predicate: str, *, accepted_only: bool = True
) -> dict[str, list[str]]:
    """Records linked to these people: {ref: [person IDs]}."""
    statuses = ("accepted",) if accepted_only else ("accepted", "suggested")
    out: dict[str, list[str]] = defaultdict(list)
    if not people:
        return out
    for subject, obj in conn.execute(
        select(assertion.c.subject_id, assertion.c.object_id).where(
            assertion.c.predicate == predicate,
            assertion.c.status.in_(statuses),
            assertion.c.reported.is_(True),
            assertion.c.subject_id.in_(sorted(set(people))),
        )
    ):
        out[obj].append(subject)
    return out


def attributions(
    conn: Connection, group_id: str, statuses: tuple[str, ...] = ("accepted",)
) -> list[dict[str, Any]]:
    query = select(assertion).where(
        assertion.c.predicate == "attributed_to",
        assertion.c.object_id == group_id,
        assertion.c.status.in_(statuses),
    )
    if statuses != ("accepted",):  # suggestions the evidence no longer supports are not shown
        query = query.where((assertion.c.status != "suggested") | assertion.c.reported.is_(True))
    return [dict(r) for r in conn.execute(query).mappings()]


def connections(
    conn: Connection, group_id: str, members: list[str], config: Config, today: date
) -> list[dict[str, Any]]:
    """Other groups (of the kinds that have papers of their own) whose current members write
    papers with this group's members."""
    if not members:
        return []
    strong = config.evidence.strong_threshold
    papers: dict[str, set[str]] = defaultdict(set)  # pmid -> authors in the directory
    for subject, obj, status, score in conn.execute(
        select(assertion.c.subject_id, assertion.c.object_id, assertion.c.status,
               assertion.c.score).where(
            assertion.c.predicate == "authored",
            assertion.c.status.in_(("accepted", "suggested")),
            assertion.c.reported.is_(True),
        )
    ):  # fmt: skip
        if status == "accepted" or (score or 0) >= strong:
            papers[obj].add(subject)
    mine = set(members)
    family = store.descendants(conn, [group_id]) | store._ancestors(conn, group_id)
    groups_of: dict[str, set[str]] = defaultdict(set)
    names: dict[str, tuple[str, str, str]] = {}
    for subject, obj, q, name, slug, kind in conn.execute(
        select(assertion.c.subject_id, assertion.c.object_id, assertion.c.qualifiers,
               unit.c.name, unit.c.slug, unit.c.kind)
        .join(unit, unit.c.id == assertion.c.object_id)
        .where(
            assertion.c.predicate == "member_of",
            assertion.c.status == "accepted",
            unit.c.status == "active",
            # Labs, programs, and the like; a whole division "works with" everyone.
            unit.c.kind.in_(config.vocabulary.attributable_kinds),
        )
    ):  # fmt: skip
        if obj not in family and is_current(q, today):
            groups_of[subject].add(obj)
            names[obj] = (name, slug, kind)
    shared: Counter = Counter()
    people: dict[str, set[str]] = defaultdict(set)
    for authors in papers.values():
        if not authors & mine:
            continue
        others = {g for p in authors - mine for g in groups_of.get(p, ())}
        for g in others:
            shared[g] += 1
            people[g] |= {p for p in authors - mine if g in groups_of.get(p, ())}
    out = []
    for g, n in shared.most_common(CONNECTIONS_SHOWN):
        name, slug, kind = names[g]
        out.append(
            {
                "id": g,
                "name": name,
                "slug": slug,
                "kind": kind,
                "kind_label": config.vocabulary.kind_label(kind),
                "papers": n,
                "people": len(people[g]),
            }
        )
    return out


__all__ = ["attributions", "connections", "linked", "people_of", "topics_and_areas"]
