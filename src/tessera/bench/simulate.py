"""A simulated research organization of any size, for measuring Tessera (PLT-14).

Shaped like a real one, from the demo's research areas: labs of a principal investigator and
their members in divisions and programs, papers that each lab writes with its own people,
other labs, and outside collaborators over 25 years (more in recent years, with a long tail of
consortium papers), outside namesakes of insiders, MeSH headings, grants that papers thank,
studies, ORCID records, and citation metrics. Family names repeat, as they do in a real
organization. Papers are generated and written in batches, so memory stays flat however many
there are. Everything is invented; identifiers are in the demo's unissued ranges.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from tessera.demo.content import AREAS, EXTERNAL_FAMILY, EXTERNAL_PLACES, FAMILY, GIVEN, Area
from tessera.demo.world import (
    CHECK,
    CITY,
    DOMAIN,
    IC_AGENCY,
    MEMBER_TITLES,
    ORG,
    PI_TITLES,
    REPORTER_ORG,
    Person,
    abbreviate,
    grant_abstract,
    grant_title,
    mesh_ui,
    orcid_id,
    paper_abstract,
    paper_title,
)

YEARS = 25  # papers span this many years, ending this year
BATCH = 50_000  # papers per lake write
MIDDLE = "ABCDEFGHJKLMNPRST"


def _headings(area: Area) -> list[str]:
    """The area's MeSH headings and more, so topics are as varied as a real organization's."""
    extra = {
        w[0].upper() + w[1:]
        for w in (*area.subjects, *area.processes, *area.models)
        if 4 <= len(w) <= 40
    }
    return list(dict.fromkeys([*area.mesh, *sorted(extra)]))


def _families(count: int, rng: random.Random) -> list[str]:
    """``count`` distinct family names: the demo's, then blends of two of them ("Okafor" and
    "Lindqvist" give "Okaqvist"), so a large organization's names are as varied as a real one's.
    Outside authors' names are kept apart (namesakes are added on purpose)."""
    out = dict.fromkeys(FAMILY)
    outside = set(EXTERNAL_FAMILY)
    pool = [f for f in FAMILY if len(f) >= 5 and f.isalpha()]
    while len(out) < count:
        a, b = rng.sample(pool, 2)
        cut_a, cut_b = rng.randint(2, len(a) - 2), rng.randint(2, len(b) - 2)
        name = a[:cut_a] + b[cut_b:].lower()
        if 4 <= len(name) <= 12 and name not in outside:
            out.setdefault(name)
    return list(out)[:count]


def _names(people: int, rng: random.Random) -> list[tuple[str, str, str]]:
    """Distinct (given, middle, family) names. Family names follow a long-tailed distribution, as
    in a real organization: a few are shared by several people, most by one."""
    families = _families(max(len(FAMILY), int(people * 0.7)), rng)
    weights = [1 / (i + 1) ** 0.6 for i in range(len(families))]
    seen: set[tuple[str, str, str]] = set()
    out = []
    while len(out) < people:
        name = (rng.choice(GIVEN), rng.choice(MIDDLE), rng.choices(families, weights=weights)[0])
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


@dataclass
class Organization:
    """The people, roster, and the records other than papers; papers come from ``papers()``."""

    people: list[Person]
    roster: list[dict[str, str]]
    reporter: list[dict[str, Any]]
    links: list[dict[str, Any]]
    studies: list[dict[str, Any]]
    namesakes: list[Person]
    seed: int
    today: date
    papers_total: int
    lab_members: dict[str, list[Person]] = field(default_factory=dict)
    grants_of_lab: dict[str, list[tuple[str, int, int]]] = field(default_factory=dict)
    start_of: dict[str, int] = field(default_factory=dict)
    papers_of: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    @property
    def pis(self) -> list[Person]:
        return [p for p in self.people if p.is_pi]


def simulate(people: int, papers: int, *, seed: int = 7, today: date | None = None) -> Organization:
    """The organization without its papers: ``people`` insiders in labs of about six."""
    today = today or date.today()
    rng = random.Random(seed)
    taken = iter(_names(people, rng))
    out: list[Person] = []
    lab_members: dict[str, list[Person]] = {}
    start_of: dict[str, int] = {}
    n = 0
    while n < people:
        area = AREAS[len(lab_members) % len(AREAS)]
        size = min(people - n, max(1, int(rng.lognormvariate(math.log(5.5), 0.45))))
        members: list[Person] = []
        for i in range(size):
            given, middle, family = next(taken)
            key = f"s{n:06d}"
            p = Person(
                key,
                given,
                middle,
                family,
                area,
                rng.choice(PI_TITLES) if i == 0 else rng.choice(MEMBER_TITLES),
                "",
                [area.division],
            )
            p.email = f"{given.lower()}.{middle.lower()}.{family.lower()}.{n}@{DOMAIN}"
            if rng.random() < 0.5:
                p.orcid = orcid_id(500_000 + n)
            members.append(p)
            n += 1
        pi = members[0]
        for p in members:
            p.lab = pi.key
            # PIs arrived long ago; members more recently.
            start_of[p.key] = today.year - (rng.randint(5, YEARS) if p is pi else rng.randint(0, 8))
        lab_members[pi.key] = members
        out.extend(members)
    roster = [
        {
            "family": p.family,
            "given": f"{p.given} {p.middle}",
            "email": p.email or "",
            "orcid": p.orcid or "",
            "unit": p.units[0],
            "title": p.title,
            "start": str(start_of[p.key]),
        }
        for p in out
    ]
    org = Organization(out, roster, [], [], [], [], seed, today, papers, lab_members, {}, start_of)
    _grants(org, rng)
    _studies(org, rng)
    # Outside researchers who share an insider's family name and first initial.
    for p in rng.sample(out, k=max(1, len(out) // 25)):
        given = rng.choice([g for g in GIVEN if g[0] == p.given[0]] or GIVEN)
        twin = Person(
            f"x{p.key}", given, rng.choice(MIDDLE), p.family, rng.choice(AREAS), "", "", []
        )
        twin.insider = False
        org.namesakes.append(twin)
    return org


def _grants(org: Organization, rng: random.Random) -> None:
    serial = 990_000
    appl = 99_000_000
    for i, pi in enumerate(org.pis):
        pi.profile_id = 90_000_000 + i
        if rng.random() > 0.7:
            continue
        for _ in range(rng.randint(1, 4)):
            serial += 1
            ic = pi.area.ic if pi.area.ic in IC_AGENCY else "GM"
            activity = rng.choice(("R01", "R01", "R01", "R21", "U01", "P01"))
            core = f"{activity}{ic}{serial}"
            start = rng.randint(org.today.year - YEARS + 2, org.today.year - 1)
            years = rng.randint(3, 5)
            amount = rng.randint(250_000, 900_000)
            title = grant_title(pi.area, rng)
            abstract = grant_abstract(pi.area, rng)
            org.grants_of_lab.setdefault(pi.key, []).append((core, start, start + years - 1))
            pi.grants.append(core)
            for fy in range(start, min(start + years, org.today.year + 1)):
                appl += 1
                org.reporter.append(
                    {
                        "appl_id": appl,
                        "project_num": f"{'1' if fy == start else '5'}{core}-{fy - start + 1:02d}",
                        "core_project_num": core,
                        "fiscal_year": fy,
                        "title": title,
                        "abstract": abstract,
                        "phr": "The results will inform new ways to prevent or treat disease.",
                        "activity_code": activity,
                        "agency_ic_admin": IC_AGENCY.get(ic, "NIGMS"),
                        "funding_mechanism": "Non-SBIR/STTR RPGs",
                        "award_amount": int(amount * (0.97 + 0.06 * rng.random())),
                        "direct_cost": int(amount * 0.66),
                        "indirect_cost": int(amount * 0.34),
                        "project_start": date(start, 7, 1),
                        "project_end": date(start + years, 6, 30),
                        "budget_start": date(fy, 7, 1),
                        "budget_end": date(fy + 1, 6, 30),
                        "is_active": start + years >= org.today.year,
                        "is_new": fy == start,
                        "org_name": REPORTER_ORG,
                        "org_city": CITY.upper(),
                        "org_country": "UNITED STATES",
                        "terms": list(pi.area.mesh[:6]),
                        "spending_categories": [],
                        "investigators": [
                            {
                                "profile_id": pi.profile_id,
                                "full_name": pi.name,
                                "family": pi.family,
                                "given": pi.given,
                                "middle": pi.middle,
                                "title": pi.title.upper(),
                                "is_contact_pi": True,
                            }
                        ],
                    }
                )


def _studies(org: Organization, rng: random.Random) -> None:
    for i, pi in enumerate(rng.sample(org.pis, k=max(1, len(org.pis) // 6))):
        start = date(rng.randint(org.today.year - 12, org.today.year - 1), rng.randint(1, 12), 1)
        title = f"A study of {rng.choice(pi.area.processes)} in {rng.choice(pi.area.models)}"
        condition = rng.choice(pi.area.conditions or ["Neoplasms"])
        org.studies.append(
            {
                "nct_id": f"NCT9{2000000 + i:07d}",
                "org_study_id": f"SIM-{start.year}-{i}",
                "brief_title": title[0].upper() + title[1:],
                "official_title": title[0].upper() + title[1:],
                "overall_status": rng.choice(("RECRUITING", "COMPLETED", "ACTIVE_NOT_RECRUITING")),
                "study_type": "INTERVENTIONAL",
                "phases": [rng.choice(("PHASE1", "PHASE2", "PHASE3"))],
                "conditions": [condition],
                "condition_mesh": [{"id": mesh_ui(condition), "term": condition}],
                "intervention_mesh": [],
                "interventions": [{"type": "DRUG", "name": f"SIM-{i:04d}"}],
                "keywords": [],
                "start_date": start,
                "start_date_type": "ACTUAL",
                "primary_completion_date": None,
                "primary_completion_date_type": None,
                "completion_date": None,
                "completion_date_type": None,
                "enrollment": rng.randint(20, 400),
                "enrollment_type": "ESTIMATED",
                "has_results": False,
                "first_posted": start,
                "last_update_posted": start,
                "brief_summary": f"This study examines {title}.",
                "lead_sponsor": ORG,
                "lead_sponsor_class": "OTHER",
                "collaborators": [],
                "officials": [
                    {
                        "seq": 1,
                        "name": f"{pi.given} {pi.middle} {pi.family}",
                        "affiliation": ORG,
                        "role": "PRINCIPAL_INVESTIGATOR",
                    },
                ],
                "sites": [
                    {
                        "seq": 1,
                        "facility": ORG,
                        "status": None,
                        "city": CITY,
                        "state": None,
                        "country": "United States",
                    }
                ],
                "references": [],
                "secondary_ids": [],
            }
        )


# --- papers ---------------------------------------------------------------------------------


def _year(rng: random.Random, today: date) -> int:
    """More papers in recent years: an organization that has grown."""
    back = int(YEARS * (1 - math.sqrt(rng.random())))
    return max(today.year - YEARS + 1, today.year - back)


def papers(
    org: Organization,
    *,
    first_pmid: int = 940_000_000,
    count: int | None = None,
    seed_offset: int = 0,
    year: int | None = None,
) -> Iterator[list[dict[str, Any]]]:
    """The organization's papers, in batches of ``BATCH`` PubMed records. Each paper's insiders
    are recorded in ``org.papers_of`` for ORCID's works lists."""
    rng = random.Random(org.seed + 1000 + seed_offset)
    year_ = year
    total = org.papers_total if count is None else count
    labs = list(org.lab_members.values())
    weights = [rng.lognormvariate(0, 0.8) * (len(m) ** 0.5) for m in labs]
    headings = {a.key: _headings(a) for a in AREAS}
    externals = [(g, f) for g in GIVEN for f in EXTERNAL_FAMILY]
    pmid = first_pmid
    batch: list[dict[str, Any]] = []
    made = 0
    while made < total:
        members = rng.choices(labs, weights=weights)[0]
        pi = members[0]
        year = year_ or _year(rng, org.today)
        present = [p for p in members if org.start_of[p.key] <= year] or [pi]
        insiders = rng.sample(present, k=min(len(present), rng.randint(1, 3)))
        if pi in present and pi not in insiders and rng.random() < 0.8:
            insiders.append(pi)  # the head of the lab, last
        if rng.random() < 0.15:  # with another lab
            other = rng.choices(labs, weights=weights)[0][0]
            if other not in insiders:
                insiders.insert(0, other)
        pmid += rng.randint(1, 3)
        n_ext = (
            int(rng.lognormvariate(math.log(3), 0.7))
            if rng.random() > 0.01
            else rng.randint(30, 80)
        )
        record = _paper(org, rng, str(pmid), year, pi.area, insiders, n_ext, externals, headings)
        for p in insiders:
            org.papers_of[p.key].append(record["pmid"])
        batch.append(record)
        made += 1
        if len(batch) >= BATCH:
            yield batch
            batch = []
    if batch:
        yield batch


def _paper(org: Organization, rng: random.Random, pmid: str, year: int, area: Area,
           insiders: list[Person], n_ext: int, externals: list[tuple[str, str]],
           headings: dict[str, list[str]]) -> dict[str, Any]:  # fmt: skip
    def affiliation(p: Person) -> list[str]:
        if rng.random() < 0.08:
            return []  # PubMed often has none for middle authors
        return [f"{p.units[0]}, {ORG}, {CITY}, USA."]

    def insider(p: Person) -> dict[str, Any]:
        full = rng.random() < 0.85
        return {
            "family": p.family,
            "given": f"{p.given} {p.middle}" if full else f"{p.given[0]}{p.middle}",
            "initials": f"{p.given[0]}{p.middle}",
            "orcid": p.orcid if p.orcid and rng.random() < 0.5 else None,
            "affiliations": affiliation(p),
        }

    def outsider() -> dict[str, Any]:
        if org.namesakes and rng.random() < 0.03:
            twin = rng.choice(org.namesakes)
            given, family, middle = twin.given, twin.family, twin.middle
        else:
            (given, family), middle = rng.choice(externals), rng.choice(MIDDLE)
        return {
            "family": family,
            "given": f"{given} {middle}",
            "initials": f"{given[0]}{middle}",
            "orcid": None,
            "affiliations": [rng.choice(EXTERNAL_PLACES)],
        }

    authors = [insider(insiders[0])]
    middle = [insider(p) for p in insiders[1:-1]] + [outsider() for _ in range(n_ext)]
    rng.shuffle(middle)
    authors += middle
    if len(insiders) > 1:
        authors.append(insider(insiders[-1]))
    count = len(authors)
    own = headings[area.key]
    chosen = rng.sample(area.mesh, min(len(area.mesh), rng.randint(2, 4)))
    chosen += rng.sample(own, min(len(own), rng.randint(2, 5)))
    chosen += CHECK["human" if rng.random() < 0.5 else "mouse"]
    journal = rng.choice(area.journals)
    record: dict[str, Any] = {
        "pmid": pmid,
        "record_type": "journal_article",
        "doi": f"10.5555/sim.{pmid}",
        "title": paper_title(area, rng),
        "abstract": paper_abstract(area, rng),
        "journal": journal,
        "journal_abbrev": abbreviate(journal),
        "volume": str(max(1, year - 1990)),
        "issue": str(rng.randint(1, 12)),
        "pages": f"{rng.randint(100, 900)}-{rng.randint(901, 990)}",
        "pub_date": date(year, 1, 1) + timedelta(days=rng.randint(0, 360)),
        "pub_year": year,
        "date_precision": "day",
        "publication_types": ["Journal Article"] + (["Review"] if rng.random() < 0.06 else []),
        "languages": ["eng"],
        "keywords": [],
        "authors": [
            {**a, "position": i, "is_first": i == 1, "is_last": i == count, "author_count": count}
            for i, a in enumerate(authors, 1)
        ],
        "mesh": [
            {"descriptor_ui": mesh_ui(d), "descriptor": d, "major_topic": i < 2}
            for i, d in enumerate(dict.fromkeys(chosen))
        ],
        "grants": [],
    }
    thanked = [
        core
        for core, start, end in org.grants_of_lab.get(insiders[-1].lab, [])
        if start <= year <= end + 2 and rng.random() < 0.5
    ][:2]
    for seq, core in enumerate(thanked, 1):
        record["grants"].append(
            {
                "seq": seq,
                "grant_id_raw": f"{core[:3]} {core[3:]}",
                "core_project_num": core,
                "ic_serial": core[3:],
                "acronym": core[3:5],
                "agency": f"{IC_AGENCY.get(core[3:5], 'NIH')} NIH HHS",
                "country": "United States",
            }
        )
        org.links.append({"pmid": pmid, "core_project_num": core, "appl_id": None})
    return record


def icite(batch: list[dict[str, Any]], today: date, rng: random.Random) -> list[dict[str, Any]]:
    out = []
    for r in batch:
        age = today.year - r["pub_year"] + 0.5
        cites = max(0, int(rng.lognormvariate(math.log(6 * age + 1), 0.8)) - 1)
        rcr = round(max(0.05, rng.lognormvariate(0.2, 0.7)), 2)
        out.append(
            {
                "pmid": r["pmid"],
                "year": r["pub_year"],
                "citation_count": cites,
                "relative_citation_ratio": rcr,
                "nih_percentile": round(min(99.9, 50 + 30 * math.log(rcr + 0.01)), 1)
                if rcr > 0.2
                else 12.0,
                "field_citation_rate": round(4 + 3 * rng.random(), 2),
                "expected_citations_per_year": round(2 + 3 * rng.random(), 2),
                "citations_per_year": round(cites / age, 2),
                "apt": 0.25,
                "is_clinical": False,
                "is_research_article": "Review" not in r["publication_types"],
            }
        )
    return out


def orcid_records(org: Organization, works: dict[str, tuple[int, str]],
                  rng: random.Random) -> list[dict[str, Any]]:  # fmt: skip
    """ORCID records for those with an iD, listing most of their recent papers (``works``: each
    paper's year and journal by PMID; ORCID matches on the PMID and DOI)."""
    out = []
    for p in org.people:
        if not p.orcid:
            continue
        mine = org.papers_of.get(p.key, [])[-250:]
        listed = [m for m in mine if m in works and rng.random() < 0.7]
        out.append(
            {
                "orcid": p.orcid,
                "status": "public",
                "primary_orcid": None,
                "given_names": f"{p.given} {p.middle}",
                "family_name": p.family,
                "credit_name": None,
                "other_names": [],
                "last_modified": f"{org.today.year}-01-15T00:00:00+00:00",
                "works": [
                    {
                        "orcid": p.orcid,
                        "put_code": k,
                        "title": f"Work {m}",
                        "type": "journal-article",
                        "pub_year": works[m][0],
                        "journal": works[m][1],
                        "doi": f"10.5555/sim.{m}",
                        "pmid": m,
                        "pmcid": None,
                        "source_name": "Crossref",
                        "versions": 1,
                    }
                    for k, m in enumerate(listed, 1)
                ],
                "employments": [
                    {
                        "orcid": p.orcid,
                        "put_code": 1,
                        "organization": ORG,
                        "city": CITY,
                        "region": None,
                        "country": "US",
                        "org_id_source": None,
                        "org_id": None,
                        "department": p.units[0],
                        "role": p.title,
                        "start_year": org.start_of[p.key],
                        "end_year": None,
                    },
                ],
            }
        )
    return out


__all__ = ["BATCH", "Organization", "icite", "orcid_records", "papers", "simulate"]
