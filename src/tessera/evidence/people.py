"""Who are our researchers? People bootstrap from public data and an optional roster.

Routes (see spec/evidence-engine.md):

* grant investigators at the organization (anchored on NIH profile IDs) -> active/former
* roster file (CSV) -> active, with unit memberships
* clusters of affiliated authors in the PubMed sweep -> candidates for an admin to promote
"""

from __future__ import annotations

import csv
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import and_, insert, select, update
from sqlalchemy.engine import Connection, Engine

from tessera.config import Config
from tessera.connectors.pubmed import normalize_orcid
from tessera.connectors.reporter import fiscal_year
from tessera.core.clock import utc_now
from tessera.core.errors import TesseraError
from tessera.core.ids import new_id
from tessera.evidence import store
from tessera.evidence.memberships import has_ended, parse_partial_date
from tessera.evidence.names import (
    Match,
    Name,
    best_match,
    family_keys,
    make_name,
    parse_display_name,
)
from tessera.evidence.orgmatch import OrgMatcher, emails_in
from tessera.lake import Lake
from tessera.ledger.tables import assertion

log = logging.getLogger("tessera.evidence.people")


def _display(first: str | None, middle: str | None, last: str | None) -> str:
    parts = [p for p in (first, middle, last) if p]
    text = " ".join(parts)
    return text.title() if text.isupper() else text


# --- grant investigators ------------------------------------------------------------------


@dataclass
class BootstrapStats:
    created: int = 0
    updated: int = 0
    names_added: int = 0
    investigators_seen: int = 0
    uncertain: int = 0  # co-investigators created as candidates

    def to_json(self) -> dict[str, int]:
        return dict(vars(self))


def bootstrap_from_reporter(
    engine: Engine, lake: Lake, matcher: OrgMatcher, today: date
) -> BootstrapStats:
    """Create or update a person for every NIH investigator on the organization's grants."""
    rows = lake.rows(
        "SELECT i.profile_id, i.full_name, i.family, i.given, i.middle, i.is_contact_pi, "
        "p.fiscal_year, p.org_name, p.subproject_id FROM reporter_investigator i "
        "JOIN reporter_project p USING (appl_id) ORDER BY i.profile_id, p.fiscal_year"
    )
    by_profile: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if matcher.matches(row["org_name"]):
            by_profile[row["profile_id"]].append(row)
    stats = BootstrapStats(investigators_seen=len(by_profile))
    current = fiscal_year(today)
    with engine.begin() as conn:
        for profile_id, records in by_profile.items():
            latest = records[-1]
            name = make_name(
                latest["family"], " ".join(p for p in (latest["given"], latest["middle"]) if p)
            )
            if name is None:
                continue
            existing = store.find_by_identifier(conn, "nih_profile", str(profile_id))
            # Multi-PI grants and subprojects list investigators from other organizations.
            # Only contact PIs of the organization's own awards are presumed to be members;
            # others start as candidates until their papers show the affiliation.
            leads = any(r["is_contact_pi"] and not r["subproject_id"] for r in records)
            if existing is None:
                if not leads:
                    status = "candidate"
                    stats.uncertain += 1
                else:
                    status = "active" if latest["fiscal_year"] >= current - 2 else "former"
                display = _display(latest["given"], latest["middle"], latest["family"])
                existing = store.create_person(
                    conn, name, status=status, source="reporter", display_name=display
                )
                store.add_identifier(
                    conn, existing, "nih_profile", str(profile_id), source="reporter"
                )
                stats.created += 1
            else:
                stats.updated += 1
            for record in records:
                variant = make_name(
                    record["family"], " ".join(p for p in (record["given"], record["middle"]) if p)
                )
                if variant and store.add_name(
                    conn, existing, variant, kind="variant", source="reporter"
                ):
                    stats.names_added += 1
    return stats


# --- roster import ------------------------------------------------------------------------


ROSTER_COLUMNS = {
    "name": ("name", "full_name", "display_name"),
    "family": ("family", "family_name", "last_name", "surname"),
    "given": ("given", "given_name", "first_name", "forename"),
    "email": ("email", "email_address"),
    "orcid": ("orcid", "orcid_id"),
    "local_id": ("local_id", "id", "employee_id", "person_id"),
    "unit": ("unit", "department", "division"),
    "unit_code": ("unit_code", "department_code"),
    "title": ("title", "position"),
    "start": ("start", "start_date", "from", "since"),
    "end": ("end", "end_date", "to", "until"),
}


@dataclass
class RosterStats:
    rows: int = 0
    created: int = 0
    matched: int = 0
    skipped: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "created": self.created,
            "matched": self.matched,
            "skipped": self.skipped[:20],
            "skipped_count": len(self.skipped),
        }


def import_roster(engine: Engine, path: Path, today: date | None = None) -> RosterStats:
    """Import people from a CSV file (an administrator's choice, not a live integration).

    Each row may give a unit with a title and the dates the person joined and left it; a
    person can appear on several rows for concurrent memberships. Someone whose memberships
    have all ended is a former member; anyone else on the roster is active.
    """
    today = today or date.today()
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise TesseraError(f"Cannot read roster {path}: {exc}") from exc
    reader = csv.DictReader(text.splitlines())
    headers = {h.strip().lower(): h for h in reader.fieldnames or []}
    columns = {
        field_: next((headers[a] for a in aliases if a in headers), None)
        for field_, aliases in ROSTER_COLUMNS.items()
    }
    if not (columns["name"] or columns["family"]):
        raise TesseraError("The roster needs a 'name' column or a 'family' (last name) column.")
    stats = RosterStats()
    touched: dict[str, bool] = {}  # person -> has a membership that is current (or undated)
    # Rows with no identifier at all are joined by full name within this file, so that a
    # person listed once per unit stays one person.
    by_name: dict[tuple[str, str], str] = {}
    with engine.begin() as conn:
        for number, row in enumerate(reader, start=2):
            stats.rows += 1

            def value(f: str, row: dict[str, str] = row) -> str:
                return (row.get(columns[f]) or "").strip() if columns[f] else ""

            if value("family"):
                name = make_name(value("family"), value("given"))
            else:
                name = parse_display_name(value("name"))
            if name is None:
                stats.skipped.append(f"line {number}: no usable name")
                continue
            email = value("email").lower()
            orcid = normalize_orcid(value("orcid")) if value("orcid") else None
            if value("orcid") and not orcid:
                stats.skipped.append(f"line {number}: invalid ORCID {value('orcid')!r}")
                continue
            local_id = value("local_id")
            try:
                start, end = parse_partial_date(value("start")), parse_partial_date(value("end"))
            except TesseraError as exc:
                stats.skipped.append(f"line {number}: {exc}")
                continue
            if start and end and start > end:
                stats.skipped.append(f"line {number}: ends ({end}) before it starts ({start})")
                continue
            identifiers = [("orcid", orcid), ("email", email or None), ("local", local_id or None)]
            person_id = next(
                (
                    pid
                    for scheme, v in identifiers
                    if v and (pid := store.find_by_identifier(conn, scheme, v))
                ),
                None,
            )
            name_key = (name.family_key, name.given.lower())
            anonymous = not any(v for _, v in identifiers)
            if person_id is None and anonymous:
                person_id = by_name.get(name_key)
            if person_id:
                stats.matched += 1
                store.add_name(conn, person_id, name, kind="variant", source="roster")
            else:
                person_id = store.create_person(conn, name, status="active", source="roster")
                stats.created += 1
            if anonymous:
                by_name.setdefault(name_key, person_id)
            for scheme, v in identifiers:
                if v:
                    store.add_identifier(conn, person_id, scheme, v, source="roster")
            current = True
            if value("unit"):
                unit_id = store.get_or_create_unit(
                    conn, value("unit"), code=value("unit_code") or None
                )
                qualifiers = {
                    k: v
                    for k, v in (("title", value("title")), ("start", start), ("end", end))
                    if v
                }
                _membership(conn, person_id, unit_id, qualifiers)
                current = not has_ended(end, today)
            touched[person_id] = touched.get(person_id, False) or current
        for person_id, current in touched.items():
            store.set_status(conn, person_id, "active" if current else "former")
    return stats


def _membership(conn: Connection, person_id: str, unit_id: str, qualifiers: dict[str, Any]) -> None:
    """One membership per person and unit; the roster is its source, so dates follow it."""
    exists = (
        conn.execute(
            select(assertion.c.id, assertion.c.qualifiers).where(
                and_(
                    assertion.c.subject_id == person_id,
                    assertion.c.predicate == "member_of",
                    assertion.c.object_id == unit_id,
                )
            )
        )
        .mappings()
        .first()
    )
    if exists:
        if (exists["qualifiers"] or {}) != qualifiers:
            conn.execute(
                update(assertion)
                .where(assertion.c.id == exists["id"])
                .values(qualifiers=qualifiers, updated_at=utc_now())
            )
        return
    now = utc_now()
    conn.execute(
        insert(assertion).values(
            id=new_id("asr"),
            subject_type="person",
            subject_id=person_id,
            predicate="member_of",
            object_type="unit",
            object_id=unit_id,
            qualifier_key="",
            qualifiers=qualifiers,
            origin="source",
            method="roster/v1",
            score=None,
            evidence=[
                {
                    "feature": "roster",
                    "value": None,
                    "weight": None,
                    "explanation": "Listed in the imported roster file",
                    "source_ref": "roster",
                }
            ],
            status="accepted",
            locked=False,
            reported=True,
            first_seen_at=now,
            last_evaluated_at=now,
            updated_at=now,
        )
    )


# --- discovery from the affiliation sweep --------------------------------------------------


@dataclass
class AffiliatedSlot:
    pmid: str
    position: int
    name: Name
    orcid: str | None
    affiliation: str
    emails: list[str]


def affiliated_slots(lake: Lake, matcher: OrgMatcher, config: Config) -> list[AffiliatedSlot]:
    """Author positions whose affiliation on that paper is the organization."""
    tokens = sorted(
        {
            w
            for phrase in (
                *config.organization.aliases,
                *config.organization.affiliation_patterns,
                config.organization.name,
                *config.organization.email_domains,
            )
            for w in [max(phrase.replace("*", " ").lower().split(), key=len, default="")]
            if len(w) >= 3
        }
    )
    if not tokens:
        return []
    # A loose pre-filter in DuckDB; the exact match happens in Python below.
    prefilter = "|".join(re.escape(t) for t in tokens)
    rows = lake.rows(
        "SELECT pmid, position, family, given, initials, orcid, affiliations FROM pubmed_author "
        "WHERE len(affiliations) > 0 AND regexp_matches("
        "lower(strip_accents(array_to_string(affiliations, ' | '))), ?)",
        [prefilter],
    )
    slots = []
    for row in rows:
        found = matcher.match_any(row["affiliations"])
        name = make_name(row["family"], row["given"], row["initials"])
        if found and name:
            slots.append(
                AffiliatedSlot(
                    row["pmid"],
                    row["position"],
                    name,
                    row["orcid"],
                    found[1],
                    [e for a in row["affiliations"] for e in emails_in(a)],
                )
            )
    return slots


def promote_investigators(engine: Engine, slots: list[AffiliatedSlot], min_papers: int) -> int:
    """Promote investigator candidates whose papers carry the organization's affiliation."""
    promoted = 0
    candidates = [p for p in store.load_people(engine, ("candidate",)) if p.ids("nih_profile")]
    by_key: dict[str, list[AffiliatedSlot]] = defaultdict(list)
    for slot in slots:
        by_key[slot.name.family_key].append(slot)
    with engine.begin() as conn:
        for record in candidates:
            papers = {
                slot.pmid
                for name in record.names
                for slot in by_key.get(name.family_key, [])
                if best_match(record.names, slot.name) is Match.FULL
            }
            if len(papers) >= min_papers:
                store.set_status(conn, record.id, "active")
                promoted += 1
    return promoted


@dataclass
class DiscoveryStats:
    affiliated_slots: int = 0
    clusters: int = 0
    candidates_created: int = 0

    def to_json(self) -> dict[str, int]:
        return dict(vars(self))


def discover_candidates(
    engine: Engine, slots: list[AffiliatedSlot], min_papers: int
) -> DiscoveryStats:
    """Propose people for affiliated authors who match nobody in the directory yet."""
    stats = DiscoveryStats(affiliated_slots=len(slots))
    # Dismissed people count as known, so a curator's "not a member" is not proposed again.
    everyone = store.load_people(engine, ("active", "former", "candidate", "dismissed"))
    index: dict[str, list[store.PersonRecord]] = defaultdict(list)
    known_orcids = {o for p in everyone for o in p.ids("orcid")}
    for record in everyone:
        for name in record.names:
            for k in family_keys(name.family):
                index[k].append(record)

    def claimed(slot: AffiliatedSlot) -> bool:
        if slot.orcid and slot.orcid in known_orcids:
            return True
        for k in family_keys(slot.name.family):
            for record in index.get(k, []):
                if best_match(record.names, slot.name) in {Match.FULL, Match.INITIALS}:
                    return True
        return False

    clusters: dict[tuple[str, ...], list[AffiliatedSlot]] = defaultdict(list)
    open_slots = [s for s in slots if not claimed(s)]
    for slot in open_slots:
        if slot.orcid:
            clusters[("orcid", slot.orcid)].append(slot)
    by_name: dict[tuple[str, str], list[AffiliatedSlot]] = defaultdict(list)
    for slot in open_slots:
        if not slot.orcid:
            by_name[(slot.name.family_key, slot.name.first_initial)].append(slot)
    for (family, initial), group in by_name.items():
        full_names = Counter(s.name.first_given for s in group if s.name.first_given)
        orcid_clusters = [
            k
            for k, v in clusters.items()
            if v[0].name.family_key == family and v[0].name.first_initial == initial
        ]
        for slot in group:
            first = slot.name.first_given
            if first:
                same = [k for k in orcid_clusters if clusters[k][0].name.first_given == first]
                target = same[0] if len(same) == 1 else ("name", family, initial, first)
            elif len(full_names) == 1:
                only = next(iter(full_names))
                same = [k for k in orcid_clusters if clusters[k][0].name.first_given == only]
                target = same[0] if len(same) == 1 else ("name", family, initial, only)
            else:
                target = ("name", family, initial, "")  # initials only, ambiguous
            clusters[target].append(slot)
    stats.clusters = len(clusters)

    with engine.begin() as conn:
        for cluster_key, members in clusters.items():
            papers = {s.pmid for s in members}
            if len(papers) < min_papers or cluster_key[-1] == "":
                continue
            names = Counter((s.name.family, s.name.given) for s in members if s.name.first_given)
            if not names:
                continue
            (family, given), _ = names.most_common(1)[0]
            name = make_name(family, given)
            if name is None:
                continue
            person_id = store.create_person(conn, name, status="candidate", source="pubmed")
            stats.candidates_created += 1
            for orcid in {s.orcid for s in members if s.orcid}:
                store.add_identifier(conn, person_id, "orcid", orcid, source="pubmed")
            for (fam, giv), _ in names.items():
                variant = make_name(fam, giv)
                if variant:
                    store.add_name(conn, person_id, variant, kind="variant", source="pubmed")
    return stats
