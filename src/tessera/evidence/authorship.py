"""Who wrote what: candidate retrieval and the ``authorship/v1`` evidence model.

Every candidate (person, paper, author position) gets a score from an additive model:
feature weights in log-odds, summed and passed through a logistic function. Each feature
that fires becomes an evidence item with a plain-language explanation, so the score can
always be explained. See spec/evidence-engine.md.

Passes:
1. Direct evidence only (ORCID, email, affiliation, grants, name, ambiguity).
2. Adds evidence learned from the person's other papers (coauthors, topics, journals),
   taken from human-accepted papers plus the previous pass's likely matches (score of at
   least SEED_THRESHOLD), repeated SEED_ROUNDS times. A paper never serves as evidence for
   itself (leave-one-out), rejected papers never do, and human decisions are never
   overridden.
"""

from __future__ import annotations

import logging
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
from sqlalchemy.engine import Engine

from tessera.config import Config
from tessera.evidence import store
from tessera.evidence.names import Match, Name, best_match, family_keys, key, make_name
from tessera.evidence.orgmatch import OrgMatcher, emails_in
from tessera.lake import Lake

log = logging.getLogger("tessera.evidence.authorship")

METHOD = "authorship/v1"
WEIGHTS = {
    "prior": -1.5,
    "orcid_match": 8.0,
    "orcid_conflict": -8.0,
    "email_match": 6.0,
    "org_affiliation": 2.5,
    "grant_link": 3.0,
    "coauthor_each": 1.0,
    "coauthor_cap": 4.0,
    "given_full_match": 1.5,
    "middle_conflict": -3.0,
    "given_conflict": -6.0,
    "topic_fit_max": 2.0,
    "journal_history": 0.5,
    "career_window": -2.0,
    "ambiguity_max": -3.0,
    "rejected_each": -1.0,
    "rejected_cap": -2.0,
    "slot_conflict": -4.0,
}
CAREER_MARGIN_YEARS = 10
# Papers this likely become evidence (coauthors, topics, journals) for the person's others.
SEED_THRESHOLD = 0.90
SEED_ROUNDS = 2
# MeSH "check tags" describe study populations, not research topics.
CHECK_TAGS = frozenset({
    "Humans", "Animals", "Male", "Female", "Adult", "Aged", "Middle Aged", "Young Adult",
    "Adolescent", "Child", "Child, Preschool", "Infant", "Infant, Newborn", "Aged, 80 and over",
    "Mice", "Rats", "Pregnancy", "Cattle", "Dogs", "Swine", "Rabbits", "Cats", "Horses",
    "Sheep", "Guinea Pigs", "Cricetinae", "Chick Embryo", "Retrospective Studies",
    "Prospective Studies", "Cohort Studies", "Follow-Up Studies", "Cross-Sectional Studies",
})  # fmt: skip

# Preprint servers and very broad journals say little about who wrote a paper.
GENERIC_VENUES = frozenset({
    "biorxiv", "medrxiv", "arxiv", "research square", "ssrn", "preprints", "sci rep",
    "plos one", "peerj", "f1000res", "cureus", "heliyon",
})  # fmt: skip
DIRECT_EVIDENCE = frozenset({"orcid_match", "email_match", "org_affiliation", "grant_link"})

_ORDER = {"orcid_match": 0, "orcid_conflict": 0, "email_match": 1}


def sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def band(score: float, config: Config) -> str:
    if score >= config.evidence.strong_threshold:
        return "strong"
    return "possible" if score >= config.evidence.review_threshold else "unlikely"


# --- data -----------------------------------------------------------------------------------


@dataclass
class Profile:
    person: store.PersonRecord
    keys: set[str]
    grants: set[str] = field(default_factory=set)  # core project numbers
    serials: set[str] = field(default_factory=set)  # institute + serial
    earliest_year: int | None = None

    @property
    def orcids(self) -> set[str]:
        return self.person.ids("orcid")

    @property
    def emails(self) -> set[str]:
        return {e.lower() for e in self.person.ids("email")}


@dataclass
class Slot:
    pmid: str
    position: int
    name: Name
    orcid: str | None
    affiliations: list[str]
    author_count: int | None


@dataclass
class Paper:
    pmid: str
    year: int | None = None
    journal: str | None = None
    mesh: set[str] = field(default_factory=set)
    cores: set[str] = field(default_factory=set)  # acknowledged grants (core numbers)
    serials: set[str] = field(default_factory=set)  # acknowledged grants (institute + serial)
    linked: set[str] = field(default_factory=set)  # RePORTER-linked core numbers
    coauthors: dict[int, str] = field(default_factory=dict)  # position -> name key


@dataclass
class Candidate:
    profile: Profile
    slot: Slot
    match: Match
    features: list[dict[str, Any]] = field(default_factory=list)
    total: float = 0.0

    @property
    def score(self) -> float:
        return round(sigmoid(self.total), 4)


@dataclass
class Seeds:
    """Aggregates over a person's evidence papers, for leave-one-out features."""

    papers: set[str] = field(default_factory=set)
    coauthors: Counter = field(default_factory=Counter)
    mesh: Counter = field(default_factory=Counter)
    journals: Counter = field(default_factory=Counter)
    rejected_coauthors: Counter = field(default_factory=Counter)


def coauthor_key(name: Name | None) -> str | None:
    return f"{name.family_key}{name.first_initial.lower()}" if name and name.first_initial else None


def arrow_family_key(values: pa.Array) -> pa.Array:
    """Vectorized ``names.key`` for an Arrow string array."""
    from tessera.evidence.names import _TRANSLATE

    out = pc.utf8_lower(values)
    for source, target in _TRANSLATE.items():
        char = chr(source)
        if char.islower():
            out = pc.replace_substring(out, char, target)
    out = pc.utf8_normalize(out, "NFKD")
    return pc.replace_substring_regex(out, r"[^a-z]", "")


# --- loading ---------------------------------------------------------------------------------


def build_profiles(lake: Lake, people: list[store.PersonRecord]) -> list[Profile]:
    profiles = []
    by_profile_id: dict[int, Profile] = {}
    for record in people:
        keys = {k for n in record.names for k in family_keys(n.family)}
        if not keys:
            continue
        profile = Profile(record, keys)
        profiles.append(profile)
        for value in record.ids("nih_profile"):
            if value.isdigit():
                by_profile_id[int(value)] = profile
    if by_profile_id:
        ids = pa.table({"profile_id": pa.array(list(by_profile_id), pa.int64())})
        cur = lake.cursor()
        try:
            cur.register("_profiles", ids)
            rows = cur.execute(
                "SELECT DISTINCT i.profile_id, p.core_project_num, year(p.project_start) AS y "
                "FROM reporter_investigator i JOIN reporter_project p USING (appl_id) "
                "JOIN _profiles USING (profile_id)"
            ).fetchall()
        finally:
            cur.close()
        for profile_id, core, year in rows:
            profile = by_profile_id[profile_id]
            if core:
                profile.grants.add(core)
                profile.serials.add(core[3:])
            if year and (profile.earliest_year is None or year < profile.earliest_year):
                profile.earliest_year = year
    return profiles


def candidate_slots(
    lake: Lake, profiles: list[Profile]
) -> dict[str, list[tuple[Profile, Slot, Match]]]:
    """Author positions whose name is compatible with one of each person's names."""
    index: dict[str, list[Profile]] = defaultdict(list)
    for profile in profiles:
        for k in profile.keys:
            index[k].append(profile)
    if not index:
        return {}
    authors = lake.arrow(
        "SELECT pmid, position, family FROM pubmed_author WHERE family IS NOT NULL"
    )
    if authors.num_rows == 0:
        return {}
    family = authors.column("family")
    keys = arrow_family_key(family)
    mask = pc.or_(
        pc.is_in(keys, value_set=pa.array(list(index))),
        pc.match_substring_regex(family, r"[-\s]"),
    )
    subset = authors.filter(mask).select(["pmid", "position"])
    cur = lake.cursor()
    try:
        cur.register("_slots", subset)
        rows = cur.execute(
            "SELECT a.pmid, a.position, a.family, a.given, a.initials, a.orcid, a.affiliations, "
            "a.author_count FROM pubmed_author a JOIN _slots USING (pmid, position)"
        ).fetchall()
    finally:
        cur.close()
    found: dict[str, list[tuple[Profile, Slot, Match]]] = defaultdict(list)
    for pmid, position, fam, given, initials, orcid, affiliations, count in rows:
        name = make_name(fam, given, initials)
        if name is None:
            continue
        slot = Slot(pmid, position, name, orcid, list(affiliations or []), count)
        seen: set[str] = set()
        for k in family_keys(fam):
            for profile in index.get(k, []):
                if profile.person.id in seen:
                    continue
                seen.add(profile.person.id)
                match = best_match(profile.person.names, name)
                if match is not Match.NONE:
                    found[profile.person.id].append((profile, slot, match))
    return found


def load_papers(lake: Lake, pmids: set[str]) -> dict[str, Paper]:
    papers = {p: Paper(p) for p in pmids}
    if not pmids:
        return papers
    cur = lake.cursor()
    try:
        cur.register("_pmids", pa.table({"pmid": pa.array(sorted(pmids), pa.string())}))
        for pmid, year, journal in cur.execute(
            "SELECT pmid, pub_year, coalesce(journal_abbrev, journal) FROM pubmed_article "
            "JOIN _pmids USING (pmid)"
        ).fetchall():
            papers[pmid].year, papers[pmid].journal = year, journal
        for pmid, descriptor in cur.execute(
            "SELECT pmid, descriptor FROM pubmed_mesh JOIN _pmids USING (pmid)"
        ).fetchall():
            if descriptor and descriptor not in CHECK_TAGS:
                papers[pmid].mesh.add(descriptor)
        for pmid, core, serial in cur.execute(
            "SELECT pmid, core_project_num, ic_serial FROM pubmed_grant JOIN _pmids USING (pmid)"
        ).fetchall():
            if core:
                papers[pmid].cores.add(core)
            if serial:
                papers[pmid].serials.add(serial)
        for pmid, core in cur.execute(
            "SELECT pmid, core_project_num FROM reporter_publication_link JOIN _pmids USING (pmid)"
        ).fetchall():
            papers[pmid].linked.add(core)
        for pmid, position, fam, given, initials in cur.execute(
            "SELECT pmid, position, family, given, initials FROM pubmed_author "
            "JOIN _pmids USING (pmid)"
        ).fetchall():
            ck = coauthor_key(make_name(fam, given, initials))
            if ck:
                papers[pmid].coauthors[position] = ck
    finally:
        cur.close()
    return papers


def ambiguity_counts(
    found: dict[str, list[tuple[Profile, Slot, Match]]],
) -> tuple[Counter, Counter]:
    """How many different people seem to share each name, from the candidate rows themselves.

    For initials, the number of distinct first names seen with the same family name and
    initial; for full names, the number of distinct ORCID iDs seen with that full name.
    """
    first_names: dict[tuple[str, str], set[str]] = defaultdict(set)
    orcids: dict[tuple[str, str], set[str]] = defaultdict(set)
    seen: set[tuple[str, int]] = set()
    for candidates in found.values():
        for _, slot, _ in candidates:
            if (slot.pmid, slot.position) in seen:
                continue
            seen.add((slot.pmid, slot.position))
            name = slot.name
            if name.first_given:
                first_names[(name.family_key, name.first_initial)].add(name.first_given)
                if slot.orcid:
                    orcids[(name.family_key, name.first_given)].add(slot.orcid)
    return (
        Counter({k: len(v) for k, v in first_names.items()}),
        Counter({k: len(v) for k, v in orcids.items()}),
    )


# --- scoring ---------------------------------------------------------------------------------


def _feature(
    features: list[dict[str, Any]],
    name: str,
    weight: float,
    value: Any,
    explanation: str,
    source_ref: str,
) -> float:
    features.append(
        {
            "feature": name,
            "value": value,
            "weight": round(weight, 3),
            "explanation": explanation,
            "source_ref": source_ref,
        }
    )
    return weight


def score_candidate(
    cand: Candidate,
    paper: Paper,
    matcher: OrgMatcher,
    ambiguity: tuple[Counter, Counter],
    seeds: Seeds | None,
) -> None:
    profile, slot = cand.profile, cand.slot
    person = profile.person.display_name
    ref = f"pubmed:{slot.pmid}#author{slot.position}"
    f: list[dict[str, Any]] = []
    total = _feature(
        f,
        "prior",
        WEIGHTS["prior"],
        None,
        "Starting point: a matching name on its own is weak evidence",
        ref,
    )

    if slot.orcid and profile.orcids:
        if slot.orcid in profile.orcids:
            total += _feature(
                f,
                "orcid_match",
                WEIGHTS["orcid_match"],
                slot.orcid,
                f"The author's ORCID iD on this paper ({slot.orcid}) is {person}'s",
                ref,
            )
        else:
            total += _feature(
                f,
                "orcid_conflict",
                WEIGHTS["orcid_conflict"],
                slot.orcid,
                f"The author's ORCID iD on this paper ({slot.orcid}) belongs to someone else",
                ref,
            )

    emails = {e for a in slot.affiliations for e in emails_in(a)}
    shared = sorted(emails & profile.emails)
    if shared:
        total += _feature(
            f,
            "email_match",
            WEIGHTS["email_match"],
            shared[0],
            f"The affiliation lists {shared[0]}, a known address for {person}",
            ref,
        )

    org = matcher.match_any(slot.affiliations)
    if org:
        snippet = org[1] if len(org[1]) <= 140 else org[1][:137] + "..."
        total += _feature(
            f,
            "org_affiliation",
            WEIGHTS["org_affiliation"],
            snippet,
            f"The author's affiliation on this paper matches {matcher.name}: “{snippet}”",
            ref,
        )

    acknowledged = sorted((paper.cores | paper.linked) & profile.grants)
    by_serial = sorted(paper.serials & profile.serials) if not acknowledged else []
    if acknowledged or by_serial:
        grant = acknowledged[0] if acknowledged else by_serial[0]
        via = (
            "NIH RePORTER links it to"
            if grant in paper.linked and grant not in paper.cores
            else "it acknowledges"
        )
        total += _feature(
            f,
            "grant_link",
            WEIGHTS["grant_link"],
            grant,
            f"The paper is tied to a grant {person} holds: {via} {grant}",
            f"grant:{grant}",
        )

    if cand.match is Match.FULL:
        total += _feature(
            f,
            "given_full_match",
            WEIGHTS["given_full_match"],
            slot.name.given,
            f"Full first name matches ({slot.name.given})",
            ref,
        )
    elif cand.match is Match.MIDDLE_CONFLICT:
        total += _feature(
            f,
            "middle_conflict",
            WEIGHTS["middle_conflict"],
            slot.name.initials,
            f"Middle initial differs (author: {slot.name.initials})",
            ref,
        )
    elif cand.match is Match.GIVEN_CONFLICT:
        total += _feature(
            f,
            "given_conflict",
            WEIGHTS["given_conflict"],
            slot.name.given,
            f"First name differs (author: {slot.name.given})",
            ref,
        )

    first_names, orcids = ambiguity
    if slot.name.first_given and cand.match is Match.FULL:
        count = orcids.get((slot.name.family_key, slot.name.first_given), 0)
        label = f"{slot.name.given.split()[0]} {slot.name.family}"
    else:
        count = first_names.get((slot.name.family_key, slot.name.first_initial), 0)
        label = f"{slot.name.family} {slot.name.first_initial}"
    if count >= 2:
        penalty = max(WEIGHTS["ambiguity_max"], -0.75 * math.log(count))
        total += _feature(
            f,
            "name_ambiguity",
            penalty,
            count,
            f"The name “{label}” belongs to about {count} different people in the data",
            ref,
        )

    if (
        paper.year
        and profile.earliest_year
        and paper.year < profile.earliest_year - CAREER_MARGIN_YEARS
    ):
        total += _feature(
            f,
            "career_window",
            WEIGHTS["career_window"],
            paper.year,
            f"Published in {paper.year}, more than {CAREER_MARGIN_YEARS} years "
            f"before {person}'s earliest known activity ({profile.earliest_year})",
            ref,
        )

    if seeds is not None:
        total += _seed_features(f, cand, paper, seeds, ref)

    f.sort(key=lambda item: (_ORDER.get(item["feature"], 2), -abs(item["weight"] or 0)))
    cand.features, cand.total = f, total


def _seed_features(
    f: list[dict[str, Any]], cand: Candidate, paper: Paper, seeds: Seeds, ref: str
) -> float:
    own = paper.pmid in seeds.papers  # leave-one-out: subtract this paper's own contribution
    my_key = coauthor_key(cand.slot.name)
    coauthors = {k for p, k in paper.coauthors.items() if p != cand.slot.position and k != my_key}
    total = 0.0

    shared = sorted(k for k in coauthors if seeds.coauthors[k] - (1 if own else 0) > 0)
    if shared:
        weight = min(WEIGHTS["coauthor_cap"], WEIGHTS["coauthor_each"] * len(shared))
        total += _feature(
            f,
            "coauthor_overlap",
            weight,
            len(shared),
            f"{len(shared)} coauthor(s) also appear on this person's other papers",
            ref,
        )
    elif coauthors:
        rejected = sorted(k for k in coauthors if seeds.rejected_coauthors[k] > 0)
        if rejected:
            weight = max(WEIGHTS["rejected_cap"], WEIGHTS["rejected_each"] * len(rejected))
            total += _feature(
                f,
                "rejected_coauthors",
                weight,
                len(rejected),
                "Coauthors overlap with papers rejected for this person",
                ref,
            )

    if paper.mesh and seeds.mesh:
        dot = sum(seeds.mesh[d] - (1 if own else 0) for d in paper.mesh)
        norm_sq = sum(v * v for v in seeds.mesh.values())
        if own:
            norm_sq += -2 * sum(seeds.mesh[d] for d in paper.mesh) + len(paper.mesh)
        if dot > 0 and norm_sq > 0:
            cosine = dot / (math.sqrt(norm_sq) * math.sqrt(len(paper.mesh)))
            weight = round(WEIGHTS["topic_fit_max"] * min(1.0, cosine), 3)
            if weight >= 0.1:
                top = sorted(paper.mesh, key=lambda d: -(seeds.mesh[d] - (1 if own else 0)))[:3]
                total += _feature(
                    f,
                    "topic_fit",
                    weight,
                    round(cosine, 3),
                    "Topics overlap with this person's other papers: " + ", ".join(top),
                    ref,
                )

    if (
        paper.journal
        and paper.journal.lower() not in GENERIC_VENUES
        and seeds.journals[paper.journal] - (1 if own else 0) > 0
    ):
        total += _feature(
            f,
            "journal_history",
            WEIGHTS["journal_history"],
            paper.journal,
            f"Has other papers in {paper.journal}",
            ref,
        )
    return total


def _seeds(
    pmids: set[str], rejected: set[str], papers: dict[str, Paper], positions: dict[str, int]
) -> Seeds:
    seeds = Seeds(papers=set(pmids))
    for pmid in pmids:
        paper = papers.get(pmid)
        if not paper:
            continue
        own = positions.get(pmid)
        seeds.coauthors.update({k for p, k in paper.coauthors.items() if p != own})
        seeds.mesh.update(paper.mesh)
        if paper.journal:
            seeds.journals[paper.journal] += 1
    for pmid in rejected:
        paper = papers.get(pmid)
        if paper:
            seeds.rejected_coauthors.update(set(paper.coauthors.values()))
    return seeds


# --- orchestration ---------------------------------------------------------------------------


@dataclass
class AuthorshipStats:
    people: int = 0
    candidates: int = 0
    strong: int = 0
    possible: int = 0
    unlikely: int = 0
    slot_conflicts: int = 0
    store: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return dict(vars(self))


def resolve_authorship(
    engine: Engine, lake: Lake, config: Config, matcher: OrgMatcher
) -> AuthorshipStats:
    people = store.load_people(engine, ("active", "former"))
    profiles = build_profiles(lake, people)
    found = candidate_slots(lake, profiles)
    pmids = {slot.pmid for cands in found.values() for _, slot, _ in cands}
    papers = load_papers(lake, pmids)
    ambiguity = ambiguity_counts(found)
    stats = AuthorshipStats(people=len(profiles))

    decided = store.assertions_for(
        engine, predicate="authored", statuses=("accepted", "rejected"), reported_only=False
    )
    accepted: dict[str, set[str]] = defaultdict(set)
    rejected: dict[str, set[str]] = defaultdict(set)
    claimed_slots: dict[tuple[str, int], str] = {}
    for row in decided:
        if not row["locked"]:
            continue
        pmid = row["object_id"].removeprefix("pub:")
        (accepted if row["status"] == "accepted" else rejected)[row["subject_id"]].add(pmid)
        position = (row["qualifiers"] or {}).get("author_position")
        if row["status"] == "accepted" and position is not None:
            claimed_slots[(pmid, int(position))] = row["subject_id"]

    # Pass 1: direct evidence.
    candidates: dict[str, list[Candidate]] = {}
    for person_id, rows in found.items():
        best: dict[str, Candidate] = {}
        for profile, slot, match in rows:
            cand = Candidate(profile, slot, match)
            score_candidate(cand, papers[slot.pmid], matcher, ambiguity, seeds=None)
            if slot.pmid not in best or cand.total > best[slot.pmid].total:
                best[slot.pmid] = cand  # one author position per person per paper
        candidates[person_id] = list(best.values())

    # Earliest activity: grants, refined by the earliest paper with direct evidence.
    for cands in candidates.values():
        years = [
            papers[c.slot.pmid].year
            for c in cands
            if c.score >= SEED_THRESHOLD
            and papers[c.slot.pmid].year
            and DIRECT_EVIDENCE & {f["feature"] for f in c.features}
        ]
        if years and cands:
            profile = cands[0].profile
            earliest = min(years)
            if profile.earliest_year is None or earliest < profile.earliest_year:
                profile.earliest_year = earliest

    # Later passes: evidence from the person's other likely papers (leave-one-out).
    for _ in range(SEED_ROUNDS):
        for person_id, cands in candidates.items():
            likely = {c.slot.pmid for c in cands if c.score >= SEED_THRESHOLD}
            seed_pmids = (accepted[person_id] | likely) - rejected[person_id]
            positions = {c.slot.pmid: c.slot.position for c in cands}
            seeds = _seeds(seed_pmids, rejected[person_id], papers, positions)
            for cand in cands:
                score_candidate(cand, papers[cand.slot.pmid], matcher, ambiguity, seeds)

    # Author-position conflicts: one author position belongs to at most one person.
    by_slot: dict[tuple[str, int], list[Candidate]] = defaultdict(list)
    for cands in candidates.values():
        for cand in cands:
            by_slot[(cand.slot.pmid, cand.slot.position)].append(cand)
    for slot_key, cands in by_slot.items():
        owner = claimed_slots.get(slot_key)
        if len(cands) < 2 and owner is None:
            continue
        ranked = sorted(cands, key=lambda c: -c.total)
        winner = owner or ranked[0].profile.person.id
        winner_name = next(
            (c.profile.person.display_name for c in cands if c.profile.person.id == winner),
            "another person",
        )
        for cand in ranked:
            if cand.profile.person.id == winner:
                continue
            stats.slot_conflicts += 1
            cand.total += _feature(
                cand.features,
                "slot_conflict",
                WEIGHTS["slot_conflict"],
                winner,
                f"{winner_name} is a stronger match for this author position",
                f"pubmed:{cand.slot.pmid}#author{cand.slot.position}",
            )

    drafts = []
    for cands in candidates.values():
        for cand in cands:
            stats.candidates += 1
            setattr(stats, band(cand.score, config), getattr(stats, band(cand.score, config)) + 1)
            slot = cand.slot
            drafts.append(
                store.Draft(
                    subject_type="person",
                    subject_id=cand.profile.person.id,
                    predicate="authored",
                    object_type="publication",
                    object_id=f"pub:{slot.pmid}",
                    origin="inferred",
                    method=METHOD,
                    score=cand.score,
                    evidence=cand.features,
                    qualifiers={
                        "author_position": slot.position,
                        "author_count": slot.author_count,
                        "author_name": f"{slot.name.family} {slot.name.initials}".strip(),
                    },
                )
            )
    result = store.sync_assertions(
        engine,
        "authored",
        drafts,
        origin="inferred",
        min_score_for_new=config.evidence.store_threshold,
    )
    stats.store = result.to_json()
    return stats


__all__ = ["METHOD", "WEIGHTS", "AuthorshipStats", "band", "key", "resolve_authorship"]
