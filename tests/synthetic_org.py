"""A synthetic research organization with known answers, for evidence-engine tests.

Everything is invented. The world contains:

* insiders at Example University: some hold NIH grants (found via RePORTER), others are
  only on the roster file; some have ORCID iDs and email addresses;
* papers by insiders: most carry the organization's affiliation, some don't (prior
  affiliation or missing affiliation); author names appear in full or as initials only;
* decoys: outside researchers who share an insider's family name and first initial, and
  some who share the *exact* full name, with different topics and coauthors;
* grants, RePORTER publication links, and a few registry studies with officials.
"""

from __future__ import annotations

import random
import zlib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from tessera.lake import LakeWriter
from tessera.lake.schemas import CTGOV, PUBMED, REPORTER, REPORTER_LINKS

ORG_AFFILIATION = "Department of {dept}, Example University, Example City, EX, USA."
OTHER_AFFILIATIONS = [
    "Department of Biology, Distant State University, Faraway, USA.",
    "Institute of Medicine, Northern College, Northtown, Canada.",
    "Faculty of Science, Southern University, Southport, Australia.",
    "Example University of Technology, Techville, USA.",  # the configured look-alike
]
TOPICS = {
    "immunology": [
        "T-Lymphocytes",
        "Immunotherapy",
        "Cytokines",
        "Antigen Presentation",
        "Immunologic Memory",
        "B-Lymphocytes",
        "Interleukin-2",
        "Vaccination",
    ],
    "genomics": [
        "Genomics",
        "Sequence Analysis, DNA",
        "Polymorphism, Single Nucleotide",
        "Genome-Wide Association Study",
        "Exome Sequencing",
        "Gene Expression Profiling",
        "Computational Biology",
        "Mutation",
    ],
    "neuro": [
        "Neurons",
        "Synapses",
        "Hippocampus",
        "Neuroplasticity",
        "Memory",
        "Dopamine",
        "Brain Mapping",
        "Neural Pathways",
    ],
    "cardio": [
        "Heart Failure",
        "Myocardium",
        "Atrial Fibrillation",
        "Hypertension",
        "Echocardiography",
        "Coronary Disease",
        "Cardiomyopathies",
        "Arrhythmias, Cardiac",
    ],
    "epi": [
        "Cohort Studies",
        "Risk Factors",
        "Incidence",
        "Prevalence",
        "Public Health",
        "Socioeconomic Factors",
        "Health Surveys",
        "Mortality",
    ],
}
OUTSIDE_TOPICS = {
    "plants": [
        "Arabidopsis",
        "Photosynthesis",
        "Plant Leaves",
        "Plant Roots",
        "Chlorophyll",
        "Seeds",
        "Plant Development",
        "Gene Expression Regulation, Plant",
    ],
    "materials": [
        "Nanoparticles",
        "Polymers",
        "Surface Properties",
        "Materials Testing",
        "Graphite",
        "Nanotubes, Carbon",
        "Electrochemistry",
        "Catalysis",
    ],
}
FAMILIES = [
    "Okafor",
    "Nguyen",
    "Silva",
    "Kowalski",
    "Haddad",
    "Takahashi",
    "Moreau",
    "Lindqvist",
    "Banerjee",
    "Osei",
    "Romero",
    "Fischer",
    "Ivanova",
    "Mensah",
    "Costa",
    "Park",
    "Novak",
    "Duarte",
    "Abebe",
    "Larsen",
    "Kaur",
    "Petrov",
    "Achterberg",
    "Rahman",
    "Weber",
    "Jansen",
    "Popescu",
    "Ozturk",
    "Kim",
    "Lopez",
    "Sato",
    "Ali",
    "Wang",
    "Smith",
    "Garcia",
    "Chen",
    "Muller",
    "Rossi",
    "Kowalczyk",
    "Andersen",
]
GIVENS = [
    "Ada",
    "Bao",
    "Carla",
    "Dmitri",
    "Elif",
    "Farah",
    "Goran",
    "Hana",
    "Ines",
    "Jonas",
    "Kofi",
    "Lena",
    "Mateo",
    "Nadia",
    "Oskar",
    "Priya",
    "Quinn",
    "Rosa",
    "Sven",
    "Tariq",
    "Uma",
    "Viktor",
    "Wen",
    "Ximena",
    "Yusuf",
    "Zara",
    "Amara",
    "Bruno",
    "Chiara",
    "Dario",
    "Esme",
    "Felix",
    "Greta",
    "Hugo",
    "Isla",
    "Jun",
    "Kira",
    "Luca",
    "Mira",
    "Nils",
]


@dataclass
class Researcher:
    key: str
    family: str
    given: str
    middle: str
    topic: str
    insider: bool
    orcid: str | None = None
    email: str | None = None
    profile_id: int | None = None
    grant: str | None = None
    dept: str = "Medicine"
    papers: list[str] = field(default_factory=list)


@dataclass
class World:
    insiders: list[Researcher]
    decoys: list[Researcher]
    truth: set[tuple[str, str]]  # (researcher key, pmid)
    roster_path: Path
    pubmed: list[dict[str, Any]]
    reporter: list[dict[str, Any]]
    links: list[dict[str, Any]]
    studies: list[dict[str, Any]]

    def insider(self, key: str) -> Researcher:
        return next(r for r in self.insiders if r.key == key)


def _orcid(rng: random.Random) -> str:
    digits = "0000" + "".join(str(rng.randrange(10)) for _ in range(12))
    return "-".join(digits[i : i + 4] for i in range(0, 16, 4))


def _ui(descriptor: str) -> str:
    return f"D{zlib.crc32(descriptor.encode()) % 10**6:06d}"


def build(tmp: Path, seed: int = 7, insiders: int = 40, hard: bool = False) -> World:
    """``hard``: name twins work in the insider's own field and publish in the same journals."""
    rng = random.Random(seed)
    people: list[Researcher] = []
    families = rng.sample(FAMILIES, insiders)
    givens = rng.sample(GIVENS, insiders)
    topics = list(TOPICS)
    for i in range(insiders):
        family, given = families[i], givens[i]
        r = Researcher(
            f"in{i:02d}",
            family,
            given,
            rng.choice("ABCDEFGHJKLMNPRST"),
            topics[i % len(topics)],
            insider=True,
            dept=rng.choice(["Medicine", "Pathology", "Neurology", "Epidemiology"]),
        )
        if rng.random() < 0.5:
            r.orcid = _orcid(rng)
        if rng.random() < 0.6:
            r.email = f"{given.lower()}.{family.lower()}@example.edu"
        if i < 30:  # grant holders; the rest are only on the roster
            r.profile_id = 10_000 + i
            r.grant = f"R01{'CA' if i % 2 else 'GM'}{100000 + i:06d}"
        people.append(r)

    decoys: list[Researcher] = []
    outside = list(OUTSIDE_TOPICS)
    for i, insider in enumerate(people[:16]):
        if i < 3:  # identical names elsewhere, middle initial included
            given, middle = insider.given, insider.middle
        elif i < 6:  # same first name, different middle initial
            given, middle = (
                insider.given,
                rng.choice([c for c in "ABCDEFGHJKLMNPRST" if c != insider.middle]),
            )
        else:  # same family name and first initial, different first name
            options = [g for g in GIVENS if g[0] == insider.given[0] and g != insider.given]
            given = options[0] if options else insider.given[0] + "xel"
            middle = rng.choice("ABCDEFGHJKLMNPRST")
        decoys.append(
            Researcher(
                f"out{i:02d}",
                insider.family,
                given,
                middle,
                outside[i % len(outside)],
                insider=False,
                orcid=_orcid(rng) if rng.random() < 0.4 else None,
            )
        )

    # Coauthor pools: insiders collaborate within their topic; decoys within theirs.
    externals = {
        t: [(f"Collab{t[:3].title()}{n}", rng.choice(GIVENS)) for n in range(8)]
        for t in [*TOPICS, *OUTSIDE_TOPICS]
    }

    pmid = 35_000_000
    articles: list[dict[str, Any]] = []
    truth: set[tuple[str, str]] = set()
    reporter_links: list[dict[str, Any]] = []

    def author(
        r: Researcher, affiliated: bool, full_name: bool, with_orcid: bool, with_email: bool
    ) -> dict[str, Any]:
        if affiliated:
            aff = ORG_AFFILIATION.format(dept=r.dept)
            if with_email and r.email:
                aff += f" {r.email}."
        else:
            aff = rng.choice(OTHER_AFFILIATIONS[:3])
        given = f"{r.given} {r.middle}" if full_name else f"{r.given[0]} {r.middle}"
        return {
            "family": r.family,
            "given": given,
            "initials": f"{r.given[0]}{r.middle}",
            "orcid": r.orcid if with_orcid else None,
            "affiliations": [aff],
        }

    def coauthor(family: str, given: str, affiliated: bool) -> dict[str, Any]:
        aff = (
            ORG_AFFILIATION.format(dept="Medicine")
            if affiliated
            else rng.choice(OTHER_AFFILIATIONS[:3])
        )
        return {
            "family": family,
            "given": given,
            "initials": given[0],
            "orcid": None,
            "affiliations": [aff],
        }

    def article(
        authors: list[dict[str, Any]], mesh: list[str], year: int, grants: list[str], journal: str
    ) -> dict[str, Any]:
        nonlocal pmid
        pmid += 1
        count = len(authors)
        record = {
            "pmid": str(pmid),
            "record_type": "journal_article",
            "title": f"Study {pmid}",
            "journal": journal,
            "journal_abbrev": journal,
            "pub_date": date(year, 6, 1),
            "pub_year": year,
            "date_precision": "month",
            "publication_types": ["Journal Article"],
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
                {"descriptor_ui": _ui(d), "descriptor": d, "major_topic": False}
                for d in ["Humans", *mesh]
            ],
            "grants": [
                {
                    "seq": i,
                    "grant_id_raw": g[:3] + " " + g[3:],
                    "core_project_num": g,
                    "ic_serial": g[3:],
                    "agency": "NIH HHS",
                }
                for i, g in enumerate(grants, 1)
            ],
        }
        articles.append(record)
        return record

    group = {t: [r for r in people if r.topic == t] for t in TOPICS}
    for r in people:
        journals = [f"J {r.topic.title()} Res", f"{r.topic.title()} Reports"]
        for _ in range(rng.randint(8, 14)):
            affiliated = rng.random() < 0.8
            mates = rng.sample(
                [m for m in group[r.topic] if m is not r], k=min(2, len(group[r.topic]) - 1)
            )
            authors = [
                author(r, affiliated, rng.random() < 0.5, rng.random() < 0.6, rng.random() < 0.3)
            ]
            authors += [
                author(m, rng.random() < 0.8, rng.random() < 0.5, rng.random() < 0.6, False)
                for m in mates
            ]
            authors += [coauthor(f, g, False) for f, g in rng.sample(externals[r.topic], k=2)]
            rng.shuffle(authors)
            grants = [r.grant] if r.grant and rng.random() < 0.5 else []
            record = article(
                authors,
                rng.sample(TOPICS[r.topic], k=4),
                rng.randint(2022, 2026),
                grants,
                rng.choice(journals),
            )
            truth.add((r.key, record["pmid"]))
            for m in mates:
                truth.add((m.key, record["pmid"]))
            r.papers.append(record["pmid"])
            if r.grant and grants and rng.random() < 0.7:
                reporter_links.append(
                    {
                        "pmid": record["pmid"],
                        "core_project_num": r.grant,
                        "appl_id": 900_000 + int(r.key[2:]),
                    }
                )

    for index, d in enumerate(decoys):
        twin = people[index]
        same_field = hard and index < 6
        topic = TOPICS[twin.topic] if same_field else OUTSIDE_TOPICS[d.topic]
        journal = f"J {twin.topic.title()} Res" if same_field else f"J {d.topic.title()} Sci"
        pool = (
            [(f"Rival{twin.topic[:3].title()}{n}", g) for n, g in enumerate(GIVENS[:8])]
            if same_field
            else externals[d.topic]
        )
        for _ in range(rng.randint(5, 10)):
            authors = [author(d, False, rng.random() < 0.5, rng.random() < 0.6, False)]
            authors += [coauthor(f, g, False) for f, g in rng.sample(pool, k=3)]
            if rng.random() < 0.15:  # a few decoys sit at the look-alike organization
                authors[0]["affiliations"] = [OTHER_AFFILIATIONS[3]]
            rng.shuffle(authors)
            article(authors, rng.sample(topic, k=4), rng.randint(2022, 2026), [], journal)

    reporter_records = []
    for r in people:
        if not r.grant:
            continue
        for fy in (2024, 2025, 2026):
            appl = 900_000 + int(r.key[2:]) * 10 + fy - 2024
            reporter_records.append(
                {
                    "appl_id": appl,
                    "project_num": f"5{r.grant}-0{fy - 2021}",
                    "core_project_num": r.grant,
                    "fiscal_year": fy,
                    "title": f"Grant {r.grant}",
                    "activity_code": "R01",
                    "award_amount": 400_000,
                    "is_active": True,
                    "org_name": "EXAMPLE UNIVERSITY",
                    "project_start": date(2023, 7, 1),
                    "project_end": date(2028, 6, 30),
                    "investigators": [
                        {
                            "profile_id": r.profile_id,
                            "full_name": f"{r.given} {r.middle} {r.family}",
                            "family": r.family,
                            "given": r.given,
                            "middle": r.middle,
                            "is_contact_pi": True,
                        }
                    ],
                }
            )

    studies = []
    for i, r in enumerate(people[:4]):
        studies.append(
            {
                "nct_id": f"NCT0900000{i}",
                "brief_title": f"Trial {i}",
                "overall_status": "RECRUITING",
                "lead_sponsor": "Example University",
                "lead_sponsor_class": "OTHER",
                "officials": [
                    {
                        "seq": 1,
                        "name": f"{r.given} {r.family}, MD",
                        "affiliation": "Example University",
                        "role": "PRINCIPAL_INVESTIGATOR",
                    }
                ],
                "sites": [{"seq": 1, "facility": "Example University Medical Center"}],
                "references": [{"pmid": r.papers[0], "type": "RESULT"}],
                "secondary_ids": [
                    {
                        "seq": 1,
                        "id": r.grant,
                        "type": "NIH",
                        "core_project_num": r.grant,
                        "ic_serial": r.grant[3:] if r.grant else None,
                    }
                ]
                if r.grant
                else [],
            }
        )

    roster = tmp / "roster.csv"
    lines = ["family,given,email,orcid,unit"]
    for r in people[30:]:
        lines.append(
            f"{r.family},{r.given} {r.middle},{r.email or ''},{r.orcid or ''},"
            f"Department of {r.dept}"
        )
    roster.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return World(people, decoys, truth, roster, articles, reporter_records, reporter_links, studies)


def load(world: World, lake_root: Path) -> None:
    with LakeWriter(lake_root, PUBMED, "run_synthetic_pubmed") as writer:
        writer.add(world.pubmed)
    with LakeWriter(lake_root, REPORTER, "run_synthetic_reporter") as writer:
        writer.add(world.reporter)
    with LakeWriter(lake_root, REPORTER_LINKS, "run_synthetic_links") as writer:
        writer.add(world.links)
    with LakeWriter(lake_root, CTGOV, "run_synthetic_ctgov") as writer:
        writer.add(world.studies)
