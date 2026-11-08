"""Which papers are a group's own (LAB-03): ``attributed_to`` links from a paper to a group.

A paper by a lab's members is not automatically the lab's: a PI writes papers with
collaborators elsewhere, and a postdoc's paper from a previous position is not this lab's.
So attribution is its own link, suggested here with its evidence and **always decided by a
person** (the group's managers, a curator, or an admin). The score only orders suggestions.

For each group of an attributable kind (``[vocabulary] attributable_kinds``: labs, programs,
centers, shared resources, and teams by default; divisions and departments would claim
everything), the candidates are papers that members wrote (confirmed, or strongly suggested)
while they belonged. The evidence:

* **member_authors**: how many of the group's members are authors. One is weak (0.30): a
  leader's authorship alone never makes a paper the group's. Two or more is good (0.55).
* **leader_senior**: a leader of the group is the last author (+0.15).
* **affiliation**: a member's affiliation on the paper names the group (+0.30).
* **member_grant**: the paper acknowledges a grant a member holds (+0.15).
* **group_topics**: the paper's MeSH headings overlap the group's topics (+0.10).

Scores are capped at 0.98. Papers with only a weak signal (below 0.30) are not recorded.
Decisions are never changed by later runs; links the evidence no longer supports are marked
as no longer reported, never deleted.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from typing import Any

import pyarrow as pa
from sqlalchemy import select
from sqlalchemy.engine import Engine

from tessera.analytics.concepts import profile_path
from tessera.config import Config
from tessera.evidence import store
from tessera.evidence.memberships import is_current
from tessera.home import DataHome
from tessera.lake import Lake
from tessera.ledger.tables import assertion, person, unit

METHOD = "attribution/v1"
MIN_SCORE = 0.30
CAP = 0.98
TOPICS_KEPT = 40  # the group's topics: its members' strongest headings
TOPIC_OVERLAP = 0.30


def _year(value: str | None, default: int) -> int:
    return int(value[:4]) if value else default


def _during(year: int | None, q: dict[str, Any]) -> bool:
    """Whether a paper from ``year`` falls in the membership (a year of slack either side:
    papers appear after the work, and work starts before a start date)."""
    if year is None:
        return True
    return _year(q.get("start"), 0) - 1 <= year <= _year(q.get("end"), 9999) + 1


def _names_group(text: str, names: list[str]) -> bool:
    lowered = text.lower()
    return any(re.search(rf"\b{re.escape(n.lower())}\b", lowered) for n in names if len(n) >= 4)


def suggest(config: Config, home: DataHome, engine: Engine, today: date | None = None) -> dict:
    """Bring ``attributed_to`` suggestions up to date for every attributable group."""
    today = today or date.today()
    kinds = config.vocabulary.attributable_kinds
    leaders = set(config.vocabulary.leader_roles)
    strong = config.evidence.strong_threshold
    with engine.connect() as conn:
        groups = {
            r.id: r
            for r in conn.execute(
                select(unit.c.id, unit.c.name, unit.c.short_name).where(
                    unit.c.status == "active", unit.c.kind.in_(kinds)
                )
            )
        }
        members: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
        for subject, obj, q in conn.execute(
            select(assertion.c.subject_id, assertion.c.object_id, assertion.c.qualifiers).where(
                assertion.c.predicate == "member_of",
                assertion.c.status == "accepted",
                assertion.c.object_id.in_(sorted(groups)),
            )
        ):
            members[obj].append((subject, q or {}))
        in_directory = set(
            conn.execute(
                select(person.c.id).where(person.c.status.in_(("active", "former")))
            ).scalars()
        )
        people = {p for rows in members.values() for p, _ in rows} & in_directory
        authored: dict[str, list[dict[str, Any]]] = defaultdict(list)  # person -> papers
        for row in conn.execute(
            select(
                assertion.c.subject_id, assertion.c.object_id, assertion.c.status,
                assertion.c.score, assertion.c.qualifiers,
            ).where(
                assertion.c.predicate == "authored",
                assertion.c.subject_id.in_(sorted(people)),
                assertion.c.status.in_(("accepted", "suggested")),
                assertion.c.reported.is_(True),
            )
        ):  # fmt: skip
            if row.status == "accepted" or (row.score or 0) >= strong:
                authored[row.subject_id].append(
                    {"pmid": row.object_id.removeprefix("pub:"), "q": row.qualifiers or {}}
                )
        grants_of: dict[str, set[str]] = defaultdict(set)
        for subject, obj in conn.execute(
            select(assertion.c.subject_id, assertion.c.object_id).where(
                assertion.c.predicate == "investigator_on",
                assertion.c.status == "accepted",
                assertion.c.subject_id.in_(sorted(people)),
            )
        ):
            grants_of[subject].add(obj)
        acknowledged: dict[str, set[str]] = defaultdict(set)  # pmid -> grants
        for subject, obj in conn.execute(
            select(assertion.c.subject_id, assertion.c.object_id).where(
                assertion.c.predicate == "acknowledges", assertion.c.status == "accepted"
            )
        ):
            acknowledged[subject.removeprefix("pub:")].add(obj)
        names = store.display_names(engine, sorted(people)) if people else {}

    pmids = sorted({p["pmid"] for papers in authored.values() for p in papers})
    years, mesh, affiliations = _paper_facts(home, pmids)
    topics = _group_topics(home, members, today)

    drafts: list[store.Draft] = []
    for gid, group in groups.items():
        group_names = [n for n in (group.name, group.short_name) if n]
        by_paper: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
        role_of: dict[str, set[str]] = defaultdict(set)
        for pid, q in members[gid]:
            if pid not in in_directory:
                continue
            role_of[pid].add(q.get("role") or config.vocabulary.default_role)
            for paper in authored.get(pid, []):
                if _during(years.get(paper["pmid"]), q):
                    by_paper[paper["pmid"]].append((pid, paper["q"]))
        group_grants: set[str] = set()
        for pid in role_of:
            group_grants |= grants_of.get(pid, set())
        for pmid, authors in by_paper.items():
            evidence, score = _evidence(
                pmid, authors, role_of, leaders, names, group_names, group_grants,
                acknowledged.get(pmid, set()), mesh.get(pmid, set()), topics.get(gid, set()),
                affiliations,
            )  # fmt: skip
            if score < MIN_SCORE:
                continue
            drafts.append(
                store.Draft(
                    subject_type="publication",
                    subject_id=f"pub:{pmid}",
                    predicate="attributed_to",
                    object_type="unit",
                    object_id=gid,
                    origin="inferred",
                    method=METHOD,
                    score=round(score, 4),
                    evidence=evidence,
                    qualifiers={"members": sorted({pid for pid, _ in authors})},
                )
            )
    stats = store.sync_assertions(engine, "attributed_to", drafts, origin="inferred")
    return {"groups": len(groups), "suggestions": len(drafts), **stats.to_json()}


def _evidence(
    pmid: str,
    authors: list[tuple[str, dict[str, Any]]],
    role_of: dict[str, set[str]],
    leaders: set[str],
    names: dict[str, str],
    group_names: list[str],
    group_grants: set[str],
    acknowledged: set[str],
    paper_mesh: set[str],
    group_topics: set[str],
    affiliations: dict[tuple[str, int], list[str]],
) -> tuple[list[dict[str, Any]], float]:
    who = sorted({pid for pid, _ in authors})
    listed = ", ".join(names.get(p, p) for p in who[:4]) + (" and others" if len(who) > 4 else "")
    weight = 0.55 if len(who) >= 2 else 0.30
    evidence = [
        {
            "feature": "member_authors",
            "value": len(who),
            "weight": weight,
            "explanation": f"Written by {listed}, {'members' if len(who) > 1 else 'a member'} "
            "of this group at the time",
            "source_ref": f"pubmed:{pmid}",
        }
    ]
    score = weight
    senior = [
        pid
        for pid, q in authors
        if role_of.get(pid, set()) & leaders
        and (q.get("author_count") or 0) > 1
        and q.get("author_position") == q.get("author_count")
    ]
    if senior:
        evidence.append(
            {
                "feature": "leader_senior",
                "value": names.get(senior[0], senior[0]),
                "weight": 0.15,
                "explanation": f"{names.get(senior[0], senior[0])}, who leads the group, is the "
                "last author",
                "source_ref": f"pubmed:{pmid}",
            }
        )
        score += 0.15
    for pid, q in authors:
        text = " ".join(affiliations.get((pmid, int(q.get("author_position") or 0)), []))
        if text and _names_group(text, group_names):
            evidence.append(
                {
                    "feature": "affiliation",
                    "value": text[:200],
                    "weight": 0.30,
                    "explanation": f"{names.get(pid, pid)}'s affiliation on the paper names the "
                    "group",
                    "source_ref": f"pubmed:{pmid}#author{q.get('author_position')}",
                }
            )
            score += 0.30
            break
    shared = sorted(acknowledged & group_grants)
    if shared:
        evidence.append(
            {
                "feature": "member_grant",
                "value": shared[0].removeprefix("grt:"),
                "weight": 0.15,
                "explanation": f"Acknowledges {shared[0].removeprefix('grt:')}, a grant held by a "
                "member",
                "source_ref": shared[0],
            }
        )
        score += 0.15
    if paper_mesh and group_topics:
        overlap = len(paper_mesh & group_topics) / len(paper_mesh)
        if overlap >= TOPIC_OVERLAP:
            evidence.append(
                {
                    "feature": "group_topics",
                    "value": round(overlap, 2),
                    "weight": 0.10,
                    "explanation": f"{round(overlap * 100)}% of its MeSH headings are among the "
                    "group's topics",
                    "source_ref": f"pubmed:{pmid}",
                }
            )
            score += 0.10
    return evidence, min(score, CAP)


def _paper_facts(
    home: DataHome, pmids: list[str]
) -> tuple[dict[str, int], dict[str, set[str]], dict[tuple[str, int], list[str]]]:
    years: dict[str, int] = {}
    mesh: dict[str, set[str]] = defaultdict(set)
    affiliations: dict[tuple[str, int], list[str]] = {}
    if not pmids:
        return years, mesh, affiliations
    with Lake(home.lake) as lake:
        cur = lake.cursor()
        try:
            cur.register("_pmids", pa.table({"pmid": pa.array(pmids, pa.string())}))
            for pmid, year in cur.execute(
                "SELECT pmid, pub_year FROM pubmed_article JOIN _pmids USING (pmid)"
            ).fetchall():
                if year:
                    years[pmid] = int(year)
            for pmid, ui in cur.execute(
                "SELECT pmid, descriptor_ui FROM pubmed_mesh JOIN _pmids USING (pmid)"
            ).fetchall():
                mesh[pmid].add(ui)
            for pmid, position, affs in cur.execute(
                "SELECT pmid, position, affiliations FROM pubmed_author JOIN _pmids USING (pmid) "
                "WHERE len(affiliations) > 0"
            ).fetchall():
                affiliations[(pmid, int(position))] = list(affs or [])
        finally:
            cur.close()
    return years, mesh, affiliations


def _group_topics(
    home: DataHome, members: dict[str, list[tuple[str, dict[str, Any]]]], today: date
) -> dict[str, set[str]]:
    """Each group's strongest headings: its current members' profiles, summed."""
    path = profile_path(home)
    if not path.is_file():
        return {}
    import pyarrow.parquet as pq

    weights: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    table = pq.read_table(path, columns=["person_id", "descriptor_ui", "weight"]).to_pylist()
    by_person: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for r in table:
        by_person[r["person_id"]].append((r["descriptor_ui"], r["weight"]))
    out: dict[str, set[str]] = {}
    for gid, rows in members.items():
        for pid, q in rows:
            if is_current(q, today):
                for ui, w in by_person.get(pid, []):
                    weights[gid][ui] += w
        ranked = sorted(weights[gid].items(), key=lambda kv: -kv[1])[:TOPICS_KEPT]
        out[gid] = {ui for ui, _ in ranked}
    return out


__all__ = ["METHOD", "suggest"]
