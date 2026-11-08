"""Possible duplicates: two records that may be the same person (``same_as/v1``).

Duplicates arise when one person reaches Tessera by two routes, most often a roster row and a
grant record that share no identifier. Each pair is scored like an authorship suggestion: a
sum of explainable evidence in log-odds, stored as a ``same_as`` suggestion for a curator. A
curator merges the records (see merge.py) or says they are different people; automation never
merges on its own.

Pairs are found by what can tie two records together: an identifier both hold (or an ORCID iD
one holds that ORCID replaced with the other's), and author positions on the same papers that
both records claim. A name alone never proposes a pair.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from tessera.config import Config
from tessera.evidence import store
from tessera.evidence.names import Match, best_match
from tessera.lake import Lake
from tessera.ledger.tables import assertion

log = logging.getLogger("tessera.evidence.duplicates")

METHOD = "same_as/v1"
PREDICATE = "same_as"
WEIGHTS = {
    "prior": -3.0,
    "shared_identifier": 8.0,
    "replaced_orcid": 8.0,
    "name_full": 2.0,
    "name_initials": 0.5,
    "middle_conflict": -2.0,
    "given_conflict": -5.0,
    "shared_position_each": 1.0,
    "shared_position_cap": 6.0,
    "shared_grant_each": 1.0,
    "shared_grant_cap": 2.0,
    "different_nih_profiles": -3.0,
    "different_orcids": -4.0,
}
STORE_MIN = 0.5  # pairs less likely than this are not kept
STATUSES = ("active", "former", "candidate")
SCHEME_LABELS = {
    "orcid": "ORCID iD",
    "email": "email",
    "nih_profile": "NIH profile",
    "local": "local ID",
}


@dataclass
class DuplicateStats:
    pairs: int = 0
    strong: int = 0
    possible: int = 0
    store: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return dict(vars(self))


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _held(record: store.PersonRecord, scheme: str) -> set[str]:
    return record.ids(scheme) | record.suggested_ids(scheme)


def resolve_duplicates(engine: Engine, lake: Lake, config: Config) -> DuplicateStats:
    people = {p.id: p for p in store.load_people(engine, STATUSES)}
    stats = DuplicateStats()
    aliases = {
        r["orcid"]: r["primary_orcid"]
        for r in lake.rows(
            "SELECT orcid, primary_orcid FROM orcid_record "
            "WHERE status = 'deprecated' AND primary_orcid IS NOT NULL"
        )
    }

    with engine.connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                select(
                    assertion.c.subject_id,
                    assertion.c.object_id,
                    assertion.c.predicate,
                    assertion.c.status,
                    assertion.c.score,
                    assertion.c.evidence,
                    assertion.c.qualifiers,
                    assertion.c.origin,
                ).where(
                    assertion.c.predicate.in_(("authored", "investigator_on", PREDICATE)),
                    assertion.c.status != "rejected",
                )
            ).mappings()
        ]
    # Author positions each record claims: accepted, or likely once the penalty for another
    # record claiming the same position is set aside (duplicates always compete for it).
    positions: dict[str, set[tuple[str, int]]] = defaultdict(set)
    grants: dict[str, set[str]] = defaultdict(set)
    existing: dict[frozenset[str], tuple[str, str, str]] = {}
    for r in [*rows, *_rejected_pairs(engine)]:
        if r["predicate"] == PREDICATE:
            existing[frozenset((r["subject_id"], r["object_id"]))] = (
                r["subject_id"],
                r["object_id"],
                r["origin"],
            )
            continue
        if r["subject_id"] not in people:
            continue
        if r["predicate"] == "investigator_on":
            grants[r["subject_id"]].add(r["object_id"])
            continue
        position = (r["qualifiers"] or {}).get("author_position")
        if position is None:
            continue
        likely = r["status"] == "accepted"
        if not likely and r["score"] is not None:
            penalty = sum(
                f["weight"] or 0 for f in r["evidence"] if f["feature"] == "slot_conflict"
            )
            adjusted = 1 / (1 + math.exp(-(_logit(r["score"]) - penalty)))
            likely = adjusted >= config.evidence.review_threshold
        if likely:
            positions[r["subject_id"]].add((r["object_id"], int(position)))

    candidates: set[frozenset[str]] = set()
    held: dict[tuple[str, str], set[str]] = defaultdict(set)
    for pid, record in people.items():
        for scheme in ("orcid", "email", "nih_profile", "local"):
            for value in _held(record, scheme):
                key = aliases.get(value, value) if scheme == "orcid" else value
                held[(scheme, key)].add(pid)
    for holders in held.values():
        candidates.update(frozenset(p) for p in combinations(sorted(holders), 2))
    by_position: dict[tuple[str, int], set[str]] = defaultdict(set)
    for pid, claimed in positions.items():
        for slot in claimed:
            by_position[slot].add(pid)
    for holders in by_position.values():
        candidates.update(frozenset(p) for p in combinations(sorted(holders), 2))

    drafts = []
    for pair in sorted(candidates, key=sorted):
        prior = existing.get(pair)
        if prior is not None and prior[2] != "inferred":
            continue  # a curator's own record of this pair
        a, b = sorted(pair)
        features, total = _score(people[a], people[b], positions, grants, aliases)
        if features is None:
            continue
        score = round(1 / (1 + math.exp(-total)), 4)
        if prior is not None:
            subject, survivor = prior[0], prior[1]
        else:
            subject, survivor = _direction(people[a], people[b], positions)
        stats.pairs += 1
        if score >= config.evidence.strong_threshold:
            stats.strong += 1
        elif score >= config.evidence.review_threshold:
            stats.possible += 1
        drafts.append(
            store.Draft(
                subject_type="person",
                subject_id=subject,
                predicate=PREDICATE,
                object_type="person",
                object_id=survivor,
                origin="inferred",
                method=METHOD,
                score=score,
                evidence=features,
                qualifiers={},
            )
        )
    stats.store = store.sync_assertions(
        engine,
        PREDICATE,
        drafts,
        origin="inferred",
        min_score_for_new=STORE_MIN,
        stale_decided=False,
    ).to_json()
    return stats


def _rejected_pairs(engine: Engine) -> list[dict[str, Any]]:
    """Pairs a curator said are different people: kept in the same direction when rescored."""
    with engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                select(
                    assertion.c.subject_id,
                    assertion.c.object_id,
                    assertion.c.predicate,
                    assertion.c.origin,
                ).where(assertion.c.predicate == PREDICATE, assertion.c.status == "rejected")
            ).mappings()
        ]


def _feature(
    features: list[dict[str, Any]], name: str, weight: float, value: Any, explanation: str
) -> float:
    features.append(
        {
            "feature": name,
            "value": value,
            "weight": round(weight, 3),
            "explanation": explanation,
            "source_ref": "ledger",
        }
    )
    return weight


def _score(
    a: store.PersonRecord,
    b: store.PersonRecord,
    positions: dict[str, set[tuple[str, int]]],
    grants: dict[str, set[str]],
    aliases: dict[str, str],
) -> tuple[list[dict[str, Any]] | None, float]:
    f: list[dict[str, Any]] = []
    total = _feature(
        f, "prior", WEIGHTS["prior"], None, "Starting point: most pairs are two people"
    )
    tied = False
    for scheme in ("nih_profile", "orcid", "email", "local"):
        shared = sorted(_held(a, scheme) & _held(b, scheme))
        if shared:
            tied = True
            total += _feature(
                f,
                "shared_identifier",
                WEIGHTS["shared_identifier"],
                shared[0],
                f"Both records hold the same {SCHEME_LABELS[scheme]} ({shared[0]})",
            )
            break
    if not tied:
        for x, y in ((a, b), (b, a)):
            replaced = sorted(o for o in _held(x, "orcid") if aliases.get(o) in _held(y, "orcid"))
            if replaced:
                tied = True
                total += _feature(
                    f,
                    "replaced_orcid",
                    WEIGHTS["replaced_orcid"],
                    replaced[0],
                    f"ORCID says {replaced[0]} was replaced by {aliases[replaced[0]]}, "
                    "which the other record holds",
                )
                break

    match = max(
        (best_match(a.names, n) for n in b.names),
        key=lambda m: _NAME_ORDER.get(m, 0),
        default=Match.NONE,
    )
    if match is Match.NONE and not tied:
        return None, 0.0  # different names and nothing tying them: not a pair
    if match is Match.FULL:
        total += _feature(f, "name_full", WEIGHTS["name_full"], None, "Full names agree")
    elif match is Match.INITIALS:
        total += _feature(
            f, "name_initials", WEIGHTS["name_initials"], None, "Names agree by initials"
        )
    elif match is Match.MIDDLE_CONFLICT:
        total += _feature(
            f, "middle_conflict", WEIGHTS["middle_conflict"], None, "Middle initials differ"
        )
    elif match is Match.GIVEN_CONFLICT or match is Match.NONE:
        total += _feature(f, "given_conflict", WEIGHTS["given_conflict"], None, "The names differ")

    shared_positions = positions[a.id] & positions[b.id]
    if shared_positions:
        n = len(shared_positions)
        weight = min(WEIGHTS["shared_position_cap"], WEIGHTS["shared_position_each"] * n)
        total += _feature(
            f,
            "shared_positions",
            weight,
            n,
            "Both records claim the author position on the same paper"
            if n == 1
            else f"Both records claim the author position on the same {n} papers",
        )
    shared_grants = grants[a.id] & grants[b.id]
    if shared_grants:
        n = len(shared_grants)
        total += _feature(
            f,
            "shared_grants",
            min(WEIGHTS["shared_grant_cap"], WEIGHTS["shared_grant_each"] * n),
            n,
            "Both are investigators on the same grant"
            if n == 1
            else f"Both are investigators on the same {n} grants",
        )
    if not tied:
        nih_a, nih_b = a.ids("nih_profile"), b.ids("nih_profile")
        if nih_a and nih_b:
            total += _feature(
                f,
                "different_nih_profiles",
                WEIGHTS["different_nih_profiles"],
                None,
                "Each record has its own NIH profile, which usually means two people",
            )
        orcid_a, orcid_b = a.ids("orcid"), b.ids("orcid")
        if orcid_a and orcid_b:
            total += _feature(
                f,
                "different_orcids",
                WEIGHTS["different_orcids"],
                None,
                "Each record has its own confirmed ORCID iD (some people have two)",
            )
    f.sort(key=lambda item: -abs(item["weight"] or 0))
    return f, total


_NAME_ORDER = {
    Match.FULL: 4,
    Match.INITIALS: 3,
    Match.MIDDLE_CONFLICT: 2,
    Match.GIVEN_CONFLICT: 1,
    Match.NONE: 0,
}


def _direction(
    a: store.PersonRecord, b: store.PersonRecord, positions: dict[str, set[tuple[str, int]]]
) -> tuple[str, str]:
    """(record to fold in, record to keep): keep a directory member over a discovered person,
    then the record claiming more papers, then the older record (IDs sort by creation time)."""
    keep = min(
        (a, b),
        key=lambda p: (p.status not in ("active", "former"), -len(positions[p.id]), p.id),
    )
    return (b if keep is a else a).id, keep.id


__all__ = ["METHOD", "WEIGHTS", "DuplicateStats", "resolve_duplicates"]
