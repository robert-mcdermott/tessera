"""What a team covers, and what it is missing (INT-10).

A team needs topics (MeSH headings: the ones its opportunity was read as, or chosen). A member
covers a topic when it, or a narrower heading beneath it, is a prominent part of their concept
profile, which comes from their accepted work; the heading and its papers are the reason
shown. For each topic nobody covers, people who do cover it are suggested, ordered by how well
they cover that gap, with their reasons, and never with a metric of the person.

It also says who on the team has worked together before (papers, grants, studies in common)
and which pairings would be new, and the groups the team spans.
"""

from __future__ import annotations

from datetime import date
from itertools import combinations
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from tessera.analytics.concepts import profile_path
from tessera.analytics.experts import ConceptReader, _exists, _expanded
from tessera.analytics.vocabulary import vocabulary_path
from tessera.evidence.memberships import is_current
from tessera.evidence.store import display_names
from tessera.home import DataHome
from tessera.lake import Lake
from tessera.ledger.tables import assertion, unit

COVERS = 0.15  # a topic is covered when it carries this much of a member's profile
SUGGESTIONS = 4
COLLABORATION_FILE = "collaboration_edge.parquet"


def _coverage(
    lake: Lake, reader: ConceptReader, concepts: list[dict[str, Any]], people: list[str]
) -> dict[tuple[str, str], dict[str, Any]]:
    """{(topic, person): their best heading for it, its weight, and its papers}."""
    if not concepts or not people:
        return {}
    wanted = [{**c, "weight": 1.0} for c in concepts]
    cur = lake.cursor()
    try:
        cur.register("_query", _expanded(wanted, reader.vocab_rows, set(reader.known)))
        import pyarrow as pa

        cur.register("_people", pa.table({"person_id": pa.array(sorted(set(people)), pa.string())}))
        rows = cur.execute(
            """
            SELECT p.person_id, q.query_ui,
                   arg_max(p.descriptor_ui, p.weight * q.factor) AS ui,
                   arg_max(p.descriptor, p.weight * q.factor) AS descriptor,
                   arg_max(p.papers, p.weight * q.factor) AS papers,
                   max(p.weight * q.factor) AS w
            FROM read_parquet(?) p JOIN _query q ON q.member_ui = p.descriptor_ui
            JOIN _people USING (person_id)
            GROUP BY p.person_id, q.query_ui
            """,
            [str(reader.profile)],
        ).fetchall()
    finally:
        cur.close()
    return {(query_ui, pid): {"descriptor_ui": ui, "descriptor": descriptor, "papers": papers,
                              "weight": round(w, 4)}
            for pid, query_ui, ui, descriptor, papers, w in rows}  # fmt: skip


def _together(home: DataHome, lake: Lake, people: list[str]) -> list[dict[str, Any]]:
    path = home.derived / COLLABORATION_FILE
    if len(people) < 2 or not _exists(path):
        return []
    cur = lake.cursor()
    try:
        marks = ", ".join("?" for _ in people)
        rows = cur.execute(
            "SELECT person_id, other_id, papers, grants, studies, last_year "  # noqa: S608
            f"FROM read_parquet(?) WHERE person_id IN ({marks}) AND other_id IN ({marks})",
            [str(path), *people, *people],
        ).fetchall()
    finally:
        cur.close()
    seen = {}
    for a, b, papers, grants, studies, last in rows:
        key = tuple(sorted((a, b)))
        seen[key] = {"papers": papers or 0, "grants": grants or 0, "studies": studies or 0,
                     "last_year": last}  # fmt: skip
    return [{"people": list(k), **v} for k, v in seen.items()]


def _groups(engine: Engine, people: list[str], today: date) -> list[dict[str, Any]]:
    if not people:
        return []
    with engine.connect() as conn:
        rows = conn.execute(
            select(assertion.c.subject_id, assertion.c.object_id, assertion.c.qualifiers).where(
                assertion.c.predicate == "member_of", assertion.c.status == "accepted",
                assertion.c.subject_id.in_(people))
        ).all()  # fmt: skip
        current: dict[str, set[str]] = {}
        for pid, gid, qualifiers in rows:
            if is_current(qualifiers or {}, today):
                current.setdefault(gid, set()).add(pid)
        info = {r.id: r for r in conn.execute(
            select(unit.c.id, unit.c.name, unit.c.slug, unit.c.kind, unit.c.status)
            .where(unit.c.id.in_(sorted(current))))}  # fmt: skip
    out = [{"id": gid, "name": info[gid].name, "slug": info[gid].slug, "kind": info[gid].kind,
            "members": sorted(ids)}
           for gid, ids in current.items()
           if gid in info and info[gid].status == "active"]  # fmt: skip
    out.sort(key=lambda g: (-len(g["members"]), g["name"]))
    return out


def analyze(home: DataHome, engine: Engine, found: dict[str, Any],
            today: date | None = None) -> dict[str, Any]:  # fmt: skip
    """Coverage, gaps with suggestions, pairings, and groups for one team (as ``store.get``)."""
    today = today or date.today()
    concepts = found["concepts"] or []
    members = [m["person_id"] for m in found["members"]]
    empty = {"ready": False, "concepts": [], "missing": [], "covered": 0, "together": [],
             "new_pairs": [], "groups": []}  # fmt: skip
    if not _exists(profile_path(home)):
        return empty
    with Lake(home.lake) as lake:
        reader = ConceptReader(lake, profile_path(home), vocabulary_path(home))
        cover = _coverage(lake, reader, concepts, members)
        rows = []
        missing = []
        for c in concepts:
            by = {pid: cover[(c["descriptor_ui"], pid)] for pid in members
                  if (c["descriptor_ui"], pid) in cover
                  and cover[(c["descriptor_ui"], pid)]["weight"] >= COVERS}  # fmt: skip
            rows.append({**c, "covered_by": by})
            if not by:
                ranked = reader.rank([{**c, "weight": 1.0}], SUGGESTIONS + len(members))
                suggested = [p for p in ranked if p["id"] not in members][:SUGGESTIONS]
                missing.append({**c, "suggestions": suggested})
        together = _together(home, lake, members)
    pairs_done = {tuple(t["people"]) for t in together}
    new_pairs = [list(p) for p in combinations(sorted(members), 2) if p not in pairs_done]
    names = display_names(engine, [*members, *(s["id"] for m in missing for s in m["suggestions"])])
    for m in missing:
        for s in m["suggestions"]:
            s["name"] = names.get(s["id"], s["id"])
    return {
        "ready": True,
        "concepts": rows,
        "missing": missing,
        "covered": sum(1 for r in rows if r["covered_by"]),
        "together": together,
        "new_pairs": new_pairs,
        "groups": _groups(engine, members, today),
        "threshold": COVERS,
    }


__all__ = ["COVERS", "analyze"]
