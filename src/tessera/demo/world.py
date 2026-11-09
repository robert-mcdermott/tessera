"""The demo organization's people and records, generated deterministically from a seed.

Labs in eight fields publish with their members, with labs in neighbouring fields, and with
outside collaborators; principal investigators hold grants and run studies. Some affiliated
authors are on no roster (discovered people), and some outside researchers share an insider's
name (namesakes), so every curation screen has something to show. Records are written in the
lake's own schemas, as the connectors would write them.

Identifiers are chosen so they can never be mistaken for real ones: PubMed IDs from
900,000,000, NCT numbers starting NCT9, grant serial numbers from 990000, ORCID iDs in the
unissued 0000-0000 block, DOIs under the 10.5555 test prefix, and emails at corvane.example.
"""

from __future__ import annotations

import math
import random
import unicodedata
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from tessera.demo.content import (
    ABSTRACT,
    AREAS,
    BRIDGES,
    CLINICAL_FALLBACK,
    CLINICAL_JOURNALS,
    CLINICAL_TITLES,
    CLINICAL_TOPICS,
    CLINICAL_TYPES,
    CLINICAL_WEIGHTS,
    CONCLUSIONS,
    CONSEQUENCES,
    DIRECTIONS,
    ELSEWHERE,
    ELSEWHERE_JOURNALS,
    EPI_ABSTRACT,
    EPI_CONCLUSIONS,
    EXTERNAL_FAMILY,
    EXTERNAL_PLACES,
    FAMILY,
    GIVEN,
    STUDIES,
    TITLES,
    VERBS,
    Area,
)

ORG = "Corvane Institute for Biomedical Research"
ORG_SHORT = "Corvane"
CITY = "Port Halden"
DOMAIN = "corvane.example"
REPORTER_ORG = ORG.upper()
IC_AGENCY = {"AI": "NIAID", "CA": "NCI", "NS": "NINDS", "GM": "NIGMS", "HL": "NHLBI"}
PI_TITLES = ("Professor", "Associate Professor", "Assistant Professor")
MEMBER_TITLES = (
    "Staff Scientist",
    "Postdoctoral Fellow",
    "Senior Research Scientist",
    "Postdoctoral Fellow",
)
CHECK = {"human": ["Humans", "Female", "Male"], "mouse": ["Animals", "Mice"]}


def mesh_ui(descriptor: str) -> str:
    """A stable, clearly invented identifier for a heading (real ones are below D100000)."""
    return f"D9{zlib.crc32(descriptor.encode()) % 10**5:05d}"


def ascii_name(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def orcid_id(n: int) -> str:
    """An iD in the 0000-0000 block, which ORCID does not issue, with a valid check digit."""
    digits = f"00000000{n:07d}"
    total = 0
    for d in digits:
        total = (total + int(d)) * 2
    check = (12 - total % 11) % 11
    full = digits + ("X" if check == 10 else str(check))
    return "-".join(full[i : i + 4] for i in range(0, 16, 4))


@dataclass
class Person:
    key: str
    given: str
    middle: str
    family: str
    area: Area
    title: str
    lab: str  # key of the lab's head
    units: list[str]
    orcid: str | None = None
    email: str | None = None
    profile_id: int | None = None  # NIH profile ID, for grant holders
    on_roster: bool = True
    insider: bool = True
    grants: list[str] = field(default_factory=list)

    @property
    def is_pi(self) -> bool:
        return self.lab == self.key

    @property
    def name(self) -> str:
        return f"{self.given} {self.middle} {self.family}"


@dataclass
class World:
    people: list[Person]
    namesakes: list[Person]
    pubmed: list[dict[str, Any]]
    icite: list[dict[str, Any]]
    reporter: list[dict[str, Any]]
    links: list[dict[str, Any]]
    studies: list[dict[str, Any]]
    orcid: list[dict[str, Any]]
    truth: set[tuple[str, str]]  # (person key, pmid)
    roster: list[dict[str, str]]
    citing: list[dict[str, Any]] = field(default_factory=list)  # clinical articles citing ours


def build_world(seed: int = 7, today: date | None = None, labs_per_area: int = 3) -> World:
    today = today or date.today()
    rng = random.Random(seed)
    first_year = today.year - 4

    # --- people ---------------------------------------------------------------------------
    # Each insider has a family name of their own, so a name in the data means one person
    # here (namesakes elsewhere are added on purpose, below).
    families = list(FAMILY)
    rng.shuffle(families)
    givens = list(GIVEN)
    rng.shuffle(givens)
    taken = iter(zip(givens * 3, families, strict=False))

    def fresh() -> tuple[str, str]:
        try:
            return next(taken)
        except StopIteration:
            raise RuntimeError("the demo ran out of names; add some to content.py") from None

    people: list[Person] = []
    counter = 0
    for area in AREAS:
        labs = labs_per_area - (
            1 if area.key in ("structural", "population") and labs_per_area > 1 else 0
        )
        for _ in range(labs):
            given, family = fresh()
            pi = Person(
                f"p{counter:03d}",
                given,
                rng.choice("ABCDEFGHJKLMNPRST"),
                family,
                area,
                rng.choice(PI_TITLES),
                "",
                [area.division],
            )
            pi.lab = pi.key
            counter += 1
            people.append(pi)
            for _ in range(rng.randint(2, 4)):
                given, family = fresh()
                member = Person(
                    f"p{counter:03d}",
                    given,
                    rng.choice("ABCDEFGHJKLMNPRST"),
                    family,
                    area,
                    rng.choice(MEMBER_TITLES),
                    pi.key,
                    [area.division],
                )
                counter += 1
                people.append(member)
    # Joint appointments where fields meet.
    for p in people:
        if p.is_pi and p.area.key == "computational" and rng.random() < 0.6:
            p.units.append("Division of Molecular Oncology")
        if p.is_pi and p.area.key == "immunotherapy" and rng.random() < 0.5:
            p.units.append("Division of Immunology and Infection")
    for i, p in enumerate(people):
        p.email = f"{ascii_name(p.given)}.{ascii_name(p.family)}@{DOMAIN}"
        if rng.random() < 0.7:
            p.orcid = orcid_id(1000 + i * 37)
        if p.is_pi:
            p.profile_id = 91_000_000 + i
    labs = {p.key: p for p in people if p.is_pi}
    members = defaultdict(list)
    for p in people:
        members[p.lab].append(p)

    # Affiliated authors on no roster or grant, for "Discovered people".
    discovered: list[Person] = []
    for lab in rng.sample(sorted(labs), 6):
        given, family = fresh()
        pi = labs[lab]
        d = Person(
            f"d{len(discovered):02d}",
            given,
            rng.choice("ABCDEFGHJKLMNPRST"),
            family,
            pi.area,
            "Research Associate",
            lab,
            [pi.area.division],
            on_roster=False,
        )
        discovered.append(d)
        members[lab].append(d)

    # Namesakes elsewhere: same family name and first initial (some, the same full name).
    namesakes: list[Person] = []
    for i, insider in enumerate(rng.sample([p for p in people if not p.is_pi], 20)):
        given = (
            insider.given
            if i < 3
            else next(
                (g for g in GIVEN if g[0] == insider.given[0] and g != insider.given), insider.given
            )
        )
        n = Person(
            f"n{i:02d}",
            given,
            rng.choice("ABCDEFGHJKLMNPRST"),
            insider.family,
            insider.area,
            "",
            f"n{i:02d}",
            [],
            on_roster=False,
            insider=False,
        )
        namesakes.append(n)

    externals = [(rng.choice(GIVEN), family) for family in EXTERNAL_FAMILY]

    # --- grants ----------------------------------------------------------------------------
    reporter: list[dict[str, Any]] = []
    grant_of_lab: dict[str, list[str]] = defaultdict(list)
    serial = 990_000
    appl = 9_900_000

    def add_grant(
        contact: Person,
        activity: str,
        others: list[Person],
        amount: int,
        start: int,
        *,
        mechanism: str = "Non-SBIR/STTR",
        years: int | None = None,
        subprojects: tuple[float, ...] = (),
        ic: str | None = None,
        source: random.Random | None = None,
        acknowledged: bool = True,
    ) -> str:
        """A grant's fiscal-year records. ``subprojects`` are the shares of each year's award
        that its components report as records of their own (the parent's amount includes them).
        """
        nonlocal serial, appl
        serial += 1
        pick = source or rng
        ic = ic or contact.area.ic
        core = f"{activity}{ic}{serial:06d}"
        end = start + (years if years is not None else (4 if activity != "K99" else 1))
        for fy in range(start, min(end, today.year) + 1):
            appl += 1
            reporter.append(
                {
                    "appl_id": appl,
                    "project_num": f"{'1' if fy == start else '5'}{core}-{fy - start + 1:02d}",
                    "core_project_num": core,
                    "fiscal_year": fy,
                    "title": grant_title(contact.area, pick),
                    "abstract": grant_abstract(contact.area, pick),
                    "phr": "The results will inform new ways to prevent, detect, or treat disease.",
                    "activity_code": activity,
                    "agency_ic_admin": IC_AGENCY[ic],
                    "funding_mechanism": mechanism,
                    "award_amount": int(amount * (0.97 + 0.06 * pick.random())),
                    "direct_cost": int(amount * 0.66),
                    "indirect_cost": int(amount * 0.34),
                    "project_start": date(start, 7, 1),
                    "project_end": date(end + 1, 6, 30),
                    "budget_start": date(fy, 7, 1),
                    "budget_end": date(fy + 1, 6, 30),
                    "is_active": end + 1 >= today.year,
                    "is_new": fy == start,
                    "org_name": REPORTER_ORG,
                    "org_city": CITY.upper(),
                    "org_country": "UNITED STATES",
                    "terms": list(contact.area.mesh[:6]),
                    "spending_categories": [],
                    "investigators": [
                        {
                            "profile_id": p.profile_id,
                            "full_name": p.name,
                            "family": p.family,
                            "given": p.given,
                            "middle": p.middle,
                            "title": p.title.upper(),
                            "is_contact_pi": p is contact,
                        }
                        for p in [contact, *others]
                    ],
                }
            )
            parent = reporter[-1]
            for n, part in enumerate(subprojects, start=1):
                appl += 1
                lead = ([*others, contact])[(n - 1) % (len(others) + 1)]
                reporter.append(
                    {
                        **parent,
                        "appl_id": appl,
                        "subproject_id": f"{n:04d}",
                        "title": f"Core {n}: {parent['title']}",
                        "award_amount": int(parent["award_amount"] * part),
                        "direct_cost": int(parent["direct_cost"] * part),
                        "indirect_cost": int(parent["indirect_cost"] * part),
                        "investigators": [
                            {**i, "is_contact_pi": True}
                            for i in parent["investigators"]
                            if i["profile_id"] == lead.profile_id
                        ],
                    }
                )
        for p in [contact, *others]:
            p.grants.append(core)
            if acknowledged:  # papers draw the grants they thank from these lists
                grant_of_lab[p.lab].append(core)
        return core

    amounts = {"R01": 520_000, "R35": 960_000, "U01": 880_000, "P01": 2_350_000, "R21": 230_000}
    for pi in labs.values():
        add_grant(
            pi,
            rng.choice(["R01", "R01", "R35"]),
            [],
            amounts["R01"],
            rng.randint(first_year, today.year - 1),
        )
        if rng.random() < 0.4:
            add_grant(pi, "R21", [], amounts["R21"], rng.randint(first_year, today.year - 1))
    for a, b in BRIDGES[:3]:
        pa = next(p for p in labs.values() if p.area.key == a)
        pb = next(p for p in labs.values() if p.area.key == b)
        add_grant(pa, "U01", [pb], amounts["U01"], first_year)
    therapy = [p for p in labs.values() if p.area.key in ("immunotherapy", "immunology")][:3]
    if len(therapy) >= 2:
        add_grant(therapy[0], "P01", therapy[1:], amounts["P01"], first_year - 1,
                  subprojects=(0.3, 0.3, 0.2))  # fmt: skip

    # Funding that shows every counting rule (a separate random stream, so that the rest of the
    # organization is the same as before): a center grant with cores that report as
    # subprojects, a supplement, institutional training, and a career award.
    extra = random.Random(seed + 101)
    oncology = sorted((p for p in labs.values() if p.area.ic == "CA"), key=lambda p: p.key)
    if oncology:
        add_grant(
            oncology[0], "P30", oncology[1:3], 1_850_000, first_year - 1,
            mechanism="Research Centers", years=5, subprojects=(0.12, 0.09), source=extra,
            acknowledged=False,
        )  # fmt: skip
    trainers = sorted(labs.values(), key=lambda p: p.key)
    add_grant(trainers[len(trainers) // 2], "T32", [], 430_000, first_year,
              mechanism="Training, Institutional", ic="GM", source=extra,
              acknowledged=False)  # fmt: skip
    fellows = sorted((p for p in people if not p.is_pi and p.title == "Postdoctoral Fellow"
                      and p.area.ic in IC_AGENCY), key=lambda p: p.key)  # fmt: skip
    if fellows:
        fellow = fellows[0]
        fellow.profile_id = 91_500_000
        add_grant(fellow, "K99", [], 125_000, today.year - 1,
                  mechanism="Other Research-Related", source=extra,
                  acknowledged=False)  # fmt: skip
    first_r01 = next(r for r in reporter if r["activity_code"] == "R01")
    r01_years = [r for r in reporter if r["core_project_num"] == first_r01["core_project_num"]]
    base = r01_years[min(2, len(r01_years) - 1)]
    appl += 1
    reporter.append(
        {
            **base,
            "appl_id": appl,
            "project_num": "3" + base["project_num"][1:] + "S1",
            "award_amount": 78_000 + extra.randint(0, 9) * 1000,
            "direct_cost": 52_000,
            "indirect_cost": 26_000 + extra.randint(0, 9) * 1000,
            "is_new": False,
        }
    )

    # --- papers ----------------------------------------------------------------------------
    pubmed: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    truth: set[tuple[str, str]] = set()
    pmid_counter = 912_340_000
    bridges = defaultdict(list)
    for a, b in BRIDGES:
        bridges[a].append(b)
        bridges[b].append(a)
    labs_by_area = defaultdict(list)
    for pi in labs.values():
        labs_by_area[pi.area.key].append(pi)
    # Some labs alike in interests never work together: those are the missed connections.
    apart = (
        {(labs_by_area["immunology"][-1].key, labs_by_area["immunotherapy"][-1].key)}
        if (labs_by_area["immunology"] and labs_by_area["immunotherapy"])
        else set()
    )

    def affiliation(p: Person, with_email: bool) -> str:
        text = f"{p.units[0]}, {ORG}, {CITY}, USA."
        return f"{text} {p.email}." if with_email and p.email else text

    def author_entry(p: Person, corresponding: bool) -> dict[str, Any]:
        full = rng.random() < 0.9
        return {
            "family": p.family,
            "given": f"{p.given} {p.middle}" if full else f"{p.given[0]}{p.middle}",
            "initials": f"{p.given[0]}{p.middle}",
            "orcid": p.orcid if p.orcid and rng.random() < 0.6 else None,
            "affiliations": [affiliation(p, corresponding)]
            if p.insider
            else [rng.choice(EXTERNAL_PLACES)],
        }

    def external_entry() -> dict[str, Any]:
        given, family = rng.choice(externals)
        initial = rng.choice("ABCDEFGHJKLMNPRST")
        return {
            "family": family,
            "given": f"{given} {initial}",
            "initials": f"{given[0]}{initial}",
            "orcid": None,
            "affiliations": [rng.choice(EXTERNAL_PLACES)],
        }

    def when(year: int) -> date:
        last = today - timedelta(days=10) if year == today.year else date(year, 12, 28)
        span = (last - date(year, 1, 1)).days
        return date(year, 1, 1) + timedelta(days=rng.randint(0, max(span, 1)))

    def add_paper(
        insiders: list[Person],
        area: Area,
        year: int,
        other: Area | None = None,
        extra_externals: int | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        nonlocal pmid_counter
        pmid_counter += rng.randint(1, 40)
        pmid = str(pmid_counter)
        n_ext = extra_externals if extra_externals is not None else rng.randint(1, 5)
        authors = [author_entry(insiders[0], False)]
        middle = [author_entry(p, False) for p in insiders[1:-1]] + [
            external_entry() for _ in range(n_ext)
        ]
        rng.shuffle(middle)
        authors += middle
        if len(insiders) > 1:
            authors.append(author_entry(insiders[-1], True))
        count = len(authors)
        headings = list(rng.sample(area.mesh, rng.randint(4, 6)))
        if other:
            headings += rng.sample(other.mesh, 2)
        headings += CHECK[
            "human"
            if area.key in ("population", "infection", "genomics", "immunotherapy")
            or rng.random() < 0.4
            else "mouse"
        ]
        pub = when(year)
        journal = rng.choice(area.journals)
        record = {
            "pmid": pmid,
            "record_type": "journal_article",
            "doi": f"10.5555/cv.{pmid}",
            "title": title or paper_title(area, rng),
            "abstract": paper_abstract(area, rng),
            "journal": journal,
            "journal_abbrev": abbreviate(journal),
            "volume": str(10 + year - 2015),
            "issue": str(rng.randint(1, 12)),
            "pages": f"{rng.randint(100, 900)}-{rng.randint(901, 990)}",
            "pub_date": pub,
            "pub_year": year,
            "date_precision": "day",
            "publication_types": ["Journal Article"] + (["Review"] if rng.random() < 0.06 else []),
            "languages": ["eng"],
            "keywords": list(rng.sample(area.keywords, min(2, len(area.keywords))))
            if area.keywords and rng.random() < 0.7
            else [],
            "authors": [
                {
                    **a,
                    "position": i,
                    "is_first": i == 1,
                    "is_last": i == count,
                    "author_count": count,
                }
                for i, a in enumerate(authors, 1)
            ],
            "mesh": [
                {"descriptor_ui": mesh_ui(d), "descriptor": d, "major_topic": i < 2}
                for i, d in enumerate(dict.fromkeys(headings))
            ],
            "grants": [],
        }
        acknowledged = sorted(
            {g for p in insiders if p.insider for g in grant_of_lab.get(p.lab, [])}
        )
        acknowledged = [g for g in acknowledged if rng.random() < 0.6][:3]
        for seq, core in enumerate(acknowledged, 1):
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
            links.append({"pmid": pmid, "core_project_num": core, "appl_id": None})
        for p in insiders:
            if p.insider:
                truth.add((p.key, pmid))
        pubmed.append(record)
        return record

    for year in range(first_year, today.year + 1):
        share = 1.0 if year < today.year else max(0.2, (today.timetuple().tm_yday / 365))
        for pi in labs.values():
            team = members[pi.key]
            for _ in range(max(1, round(rng.randint(4, 7) * share))):
                first = rng.choice([m for m in team if m is not pi] or [pi])
                others = (
                    rng.sample(
                        [m for m in team if m not in (pi, first)],
                        k=min(len(team) - 2, rng.randint(0, 2)),
                    )
                    if len(team) > 2
                    else []
                )
                cast = [first, *others]
                other_area = None
                roll = rng.random()
                if roll < 0.12 and len(labs_by_area[pi.area.key]) > 1:
                    peer = rng.choice([p for p in labs_by_area[pi.area.key] if p is not pi])
                    if (pi.key, peer.key) not in apart and (peer.key, pi.key) not in apart:
                        cast.append(peer)
                elif roll < 0.2 and bridges[pi.area.key]:
                    partner_area = rng.choice(bridges[pi.area.key])
                    candidates = [
                        p
                        for p in labs_by_area[partner_area]
                        if (pi.key, p.key) not in apart and (p.key, pi.key) not in apart
                    ]
                    if candidates:
                        peer = rng.choice(candidates)
                        cast.append(peer)
                        other_area = peer.area
                cast.append(pi)
                add_paper(cast, pi.area, year, other_area)
        if year == today.year - 2:  # one consortium paper: many members, many outside authors
            cast = rng.sample([p for p in people if p.is_pi], 8)
            add_paper(
                cast,
                rng.choice(AREAS),
                year,
                rng.choice(AREAS),
                extra_externals=18,
                title=CONSORTIUM_TITLE,
            )

    for i, n in enumerate(namesakes):  # namesakes publish elsewhere, under outside affiliations
        near = (
            i % 2 == 0
        )  # half work in a neighbouring field, which makes them harder to tell apart
        topic = rng.sample(n.area.mesh, 4) if near else ELSEWHERE[i % len(ELSEWHERE)]
        for _ in range(rng.randint(4, 8)):
            pmid_counter += rng.randint(1, 40)
            pmid = str(pmid_counter)
            year = rng.randint(first_year, today.year)
            authors = [external_entry() for _ in range(rng.randint(2, 5))]
            # Most namesakes appear under initials only, as many papers print them.
            shown = f"{n.given} {n.middle}" if i % 3 == 0 else f"{n.given[0]} {n.middle}"
            authors.insert(
                rng.randint(0, len(authors)),
                {
                    "family": n.family,
                    "given": shown,
                    "initials": f"{n.given[0]}{n.middle}",
                    "orcid": None,
                    "affiliations": [rng.choice(EXTERNAL_PLACES)],
                },
            )
            count = len(authors)
            pubmed.append(
                {
                    "pmid": pmid,
                    "record_type": "journal_article",
                    "doi": f"10.5555/cv.{pmid}",
                    "title": paper_title(n.area, rng) if near else elsewhere_title(topic[0], rng),
                    "abstract": paper_abstract(n.area, rng) if near else "",
                    "journal": rng.choice(n.area.journals)
                    if near
                    else ELSEWHERE_JOURNALS[i % len(ELSEWHERE_JOURNALS)],
                    "journal_abbrev": abbreviate(ELSEWHERE_JOURNALS[i % len(ELSEWHERE_JOURNALS)]),
                    "pub_date": when(year),
                    "pub_year": year,
                    "date_precision": "day",
                    "publication_types": ["Journal Article"],
                    "languages": ["eng"],
                    "keywords": [],
                    "authors": [
                        {
                            **a,
                            "position": k,
                            "is_first": k == 1,
                            "is_last": k == count,
                            "author_count": count,
                        }
                        for k, a in enumerate(authors, 1)
                    ],
                    "mesh": [
                        {"descriptor_ui": mesh_ui(d), "descriptor": d, "major_topic": k < 2}
                        for k, d in enumerate(topic)
                    ],
                    "grants": [],
                }
            )

    # --- citation metrics ------------------------------------------------------------------
    icite = []
    for r in pubmed:
        age = today.year - r["pub_year"] + 0.5
        expected = 6 * age
        cites = max(0, int(rng.lognormvariate(math.log(expected + 1), 0.8)) - 1)
        rcr = round(max(0.05, rng.lognormvariate(0.2, 0.7)), 2)
        icite.append(
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
                "apt": round(rng.choice([0.05, 0.25, 0.5, 0.75, 0.95]), 2),
                "is_clinical": False,
                "is_research_article": "Review" not in r["publication_types"],
            }
        )

    # Clinical articles citing the papers, from a separate random stream so nothing else moves.
    citing = clinical_citations(pubmed, icite, reporter, links, today, random.Random(seed + 202))

    # --- studies ---------------------------------------------------------------------------
    studies = []
    papers_of = defaultdict(list)
    for key, pmid in sorted(truth):
        papers_of[key].append(pmid)
    for i, s in enumerate(STUDIES):
        area_pis = labs_by_area.get(s["area"]) or list(labs.values())
        pi = area_pis[i % len(area_pis)]
        start = date(rng.randint(first_year - 1, today.year - 1), rng.randint(1, 12), 1)
        done = s["status"] == "COMPLETED"
        studies.append(
            {
                "nct_id": f"NCT9{1000000 + i * 4211:07d}",
                "org_study_id": f"CV-{start.year}-{100 + i}",
                "brief_title": s["title"],
                "official_title": s["title"],
                "overall_status": s["status"],
                "study_type": s.get("study_type", "INTERVENTIONAL"),
                "phases": s["phases"],
                "conditions": s["conditions"],
                "condition_mesh": [{"id": mesh_ui(c), "term": c} for c in s["conditions"]],
                "intervention_mesh": [{"id": mesh_ui(t), "term": t} for t in s["interventions"]],
                "interventions": [
                    {"type": kind, "name": t}
                    for t, kind in zip(s["interventions"], s["types"], strict=True)
                ],
                "keywords": [],
                "lead_sponsor": s.get("sponsor", ORG),
                "lead_sponsor_class": s.get("sponsor_class", "OTHER"),
                "collaborators": ["National Cancer Institute (NCI)"] if pi.area.ic == "CA" else [],
                "start_date": start,
                "start_date_type": "ACTUAL",
                "primary_completion_date": start + timedelta(days=900),
                "primary_completion_date_type": "ACTUAL" if done else "ESTIMATED",
                "completion_date": start + timedelta(days=1100),
                "completion_date_type": "ACTUAL" if done else "ESTIMATED",
                "enrollment": s["enrollment"],
                "enrollment_type": "ACTUAL" if done else "ESTIMATED",
                "has_results": done,
                "first_posted": start - timedelta(days=40),
                "last_update_posted": today - timedelta(days=rng.randint(5, 200)),
                "brief_summary": (
                    f"This study evaluates {s['interventions'][0].lower()} "
                    f"in people with {s['conditions'][0].lower()}."
                ),
                "officials": [
                    {
                        "seq": 1,
                        "name": f"{pi.given} {pi.family}, MD, PhD",
                        "affiliation": ORG,
                        "role": "PRINCIPAL_INVESTIGATOR",
                    }
                ],
                "sites": [
                    {
                        "seq": 1,
                        "facility": f"{ORG_SHORT} Medical Center",
                        "status": "RECRUITING" if not done else None,
                        "city": CITY,
                        "state": None,
                        "country": "United States",
                    }
                ]
                + [
                    {
                        "seq": k + 2,
                        "facility": place.split(", ")[1],
                        "status": None,
                        "city": place.split(", ")[2],
                        "state": None,
                        "country": place.split(", ")[3].rstrip("."),
                    }
                    for k, place in enumerate(rng.sample(EXTERNAL_PLACES, 2))
                ],
                "references": [
                    {"pmid": p, "type": "RESULT" if done else "BACKGROUND", "citation": None}
                    for p in papers_of[pi.key][:2]
                ],
                "secondary_ids": [
                    {
                        "seq": 1,
                        "id": pi.grants[0],
                        "type": "NIH",
                        "core_project_num": pi.grants[0],
                        "ic_serial": pi.grants[0][3:],
                    }
                ]
                if pi.grants
                else [],
            }
        )

    # --- ORCID records ---------------------------------------------------------------------
    orcid = []
    for p in people:
        if not p.orcid:
            continue
        mine = [r for r in pubmed if (p.key, r["pmid"]) in truth]
        listed = [r for r in mine if rng.random() < 0.75]
        orcid.append(
            {
                "orcid": p.orcid,
                "status": "public",
                "primary_orcid": None,
                "given_names": f"{p.given} {p.middle}",
                "family_name": p.family,
                "credit_name": None,
                "other_names": [],
                "last_modified": f"{today.year}-{max(1, today.month - 1):02d}-15T00:00:00+00:00",
                "works": [
                    {
                        "orcid": p.orcid,
                        "put_code": k,
                        "title": r["title"],
                        "type": "journal-article",
                        "pub_year": r["pub_year"],
                        "journal": r["journal"],
                        "doi": r["doi"],
                        "pmid": r["pmid"],
                        "pmcid": None,
                        "source_name": "Crossref",
                        "versions": 1,
                    }
                    for k, r in enumerate(listed, 1)
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
                        "start_year": first_year - rng.randint(0, 8),
                        "end_year": None,
                    }
                ],
            }
        )

    roster = [
        {
            "family": p.family,
            "given": f"{p.given} {p.middle}",
            "email": p.email or "",
            "orcid": p.orcid if p.orcid and not p.is_pi and rng.random() < 0.6 else "",
            "unit": unit,
            "title": p.title,
            "start": str(first_year - rng.randint(0, 9)),
        }
        for p in people
        for unit in p.units
    ]
    return World(
        people + discovered,
        namesakes,
        pubmed,
        icite,
        reporter,
        links,
        studies,
        orcid,
        truth,
        roster,
        citing,
    )


def clinical_citations(
    pubmed: list[dict[str, Any]],
    icite: list[dict[str, Any]],
    reporter: list[dict[str, Any]],
    links: list[dict[str, Any]],
    today: date,
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Clinical articles that cite the papers, recorded on the papers' iCite rows.

    Papers with a higher translation score (iCite's APT) are cited more often; some articles
    cite several papers of the same field. Two papers that thank the program project (P01) are
    cited by one practice guideline, so its thread always reaches one."""
    area_of = {d: a for a in AREAS for d in a.mesh}
    by_pmid = {r["pmid"]: r for r in icite}
    for r in icite:
        r["clinical_citations"] = []
    rates = {0.95: 0.3, 0.75: 0.2, 0.5: 0.1, 0.25: 0.03, 0.05: 0.01}
    kinds = list(CLINICAL_WEIGHTS)
    weights = [CLINICAL_WEIGHTS[k] for k in kinds]
    articles: list[dict[str, Any]] = []
    pool: dict[str, list[dict[str, Any]]] = defaultdict(list)
    counter = 915_600_000
    titles: set[str] = set()

    def topic(area: Area) -> str:
        names = [CLINICAL_TOPICS.get(c, c.lower()) for c in area.conditions]
        return rng.choice(names or list(CLINICAL_FALLBACK.get(area.key, ("chronic disease",))))

    def article(kind: str, area: Area, year: int) -> dict[str, Any]:
        nonlocal counter
        counter += rng.randint(1, 30)
        title = rng.choice(CLINICAL_TITLES[kind]).format(c=topic(area))
        for _ in range(6):  # titles are drawn from a few templates; avoid repeating one
            if title not in titles:
                break
            title = rng.choice(CLINICAL_TITLES[kind]).format(c=topic(area))
        titles.add(title)
        found = {
            "pmid": str(counter),
            "title": title,
            "journal": rng.choice(CLINICAL_JOURNALS),
            "journal_abbrev": None,
            "pub_year": year,
            "pub_date": date(year, rng.randint(1, 12), 1),
            "doi": None,
            "publication_types": list(CLINICAL_TYPES[kind]),
            "first_author": f"{rng.choice(EXTERNAL_FAMILY)} {rng.choice('ABCDEFGHJKLMNPRSTW')}",
            "author_count": rng.randint(4, 24),
            "is_retracted": False,
        }
        articles.append(found)
        pool[area.key].append(found)
        return found

    def cite(pmid: str, found: dict[str, Any]) -> None:
        row = by_pmid[pmid]["clinical_citations"]
        if all(c["citing_pmid"] != found["pmid"] for c in row):
            row.append({"citing_pmid": found["pmid"]})

    for r in pubmed:
        year = r["pub_year"]
        area = next((area_of[m["descriptor"]] for m in r["mesh"] if m["descriptor"] in area_of),
                    None)  # fmt: skip
        if area is None or r["pmid"] not in by_pmid or year > today.year - 1:
            continue
        rate = rates.get(by_pmid[r["pmid"]]["apt"], 0.05)
        for _ in range(4):
            if rng.random() >= rate:
                continue
            when = year + rng.randint(1, 4)
            if when > today.year:
                continue
            later = [a for a in pool[area.key] if a["pub_year"] > year]
            if later and rng.random() < 0.35:
                cite(r["pmid"], rng.choice(later))
            else:
                cite(r["pmid"], article(rng.choices(kinds, weights)[0], area, when))

    program = sorted({x["core_project_num"] for x in reporter if x["activity_code"] == "P01"})
    mesh_of = {r["pmid"]: r["mesh"] for r in pubmed}
    if program:
        core = program[0]
        thanked = {x["pmid"] for x in links if x["core_project_num"] == core}
        thanked |= {r["pmid"] for r in pubmed
                    if any(g.get("core_project_num") == core for g in r["grants"])}  # fmt: skip
        settled = [p for p in thanked if p in by_pmid and by_pmid[p]["year"] <= today.year - 2]
        older = sorted(settled, key=int)
        if len(older) >= 2:
            when = min(today.year, max(by_pmid[p]["year"] for p in older[:2]) + 2)
            field = next((area_of[m["descriptor"]] for m in mesh_of.get(older[0], [])
                          if m["descriptor"] in area_of), AREAS[0])  # fmt: skip
            guideline = article("guideline", field, when)
            for p in older[:2]:
                cite(p, guideline)
    for r in icite:
        r["clinical_citations"].sort(key=lambda c: int(c["citing_pmid"]))
    return sorted(articles, key=lambda a: int(a["pmid"]))


CONSORTIUM_TITLE = (
    "A multi-institutional atlas of immune and tumor cell states across human cancers"
)


def elsewhere_title(topic: str, rng: random.Random) -> str:
    opening = rng.choice(["Variation in", "Mechanisms of", "Long-term changes in"])
    closing = rng.choice(["under stress", "across sites", "in field conditions"])
    return f"{opening} {topic.lower()} {closing}"


def paper_title(area: Area, rng: random.Random) -> str:
    template = rng.choice(area.titles or TITLES)
    text = template.format(
        S=rng.choice(area.subjects),
        P=rng.choice(area.processes),
        M=rng.choice(area.models),
        T=rng.choice(area.methods),
        t=rng.choice(area.methods).lower(),
        F=rng.choice(area.findings),
    )
    return text[0].upper() + text[1:]


def paper_abstract(area: Area, rng: random.Random) -> str:
    subject = rng.choice(area.subjects)
    values = {
        "B": rng.choice(area.background),
        "S": subject,
        "S_cap": subject[0].upper() + subject[1:],
        "P": rng.choice(area.processes),
        "M": rng.choice(area.models),
        "t": rng.choice(area.methods).lower(),
        "F": rng.choice(area.findings),
        "verb": rng.choice(VERBS),
        "consequence": rng.choice(CONSEQUENCES),
        "conclusion": rng.choice(CONCLUSIONS),
        "conclusion_epi": rng.choice(EPI_CONCLUSIONS),
        "direction": rng.choice(DIRECTIONS),
    }
    template = EPI_ABSTRACT if area.key == "population" else ABSTRACT
    return " ".join(s.format(**values) for s in template)


def grant_title(area: Area, rng: random.Random) -> str:
    options = (
        f"Mechanisms of {rng.choice(area.processes)} in {rng.choice(area.models)}",
        f"{rng.choice(area.subjects)} and {rng.choice(area.processes)}",
        f"Targeting {rng.choice(area.subjects)} to improve {rng.choice(area.processes)}",
    )
    text = rng.choice(options)
    return text[0].upper() + text[1:]


def grant_abstract(area: Area, rng: random.Random) -> str:
    return (
        f"{rng.choice(area.background)} This project will define how {rng.choice(area.subjects)} "
        f"governs {rng.choice(area.processes)}, using {rng.choice(area.methods).lower()} in "
        f"{rng.choice(area.models)}. {rng.choice(CONCLUSIONS)}"
    )


def abbreviate(journal: str) -> str:
    short = {
        "Journal": "J",
        "Annals": "Ann",
        "Reports": "Rep",
        "Letters": "Lett",
        "Review": "Rev",
        "Research": "Res",
        "Immunology": "Immunol",
        "Biology": "Biol",
        "Science": "Sci",
    }
    return " ".join(short.get(w, w) for w in journal.split() if w not in ("of", "and"))
