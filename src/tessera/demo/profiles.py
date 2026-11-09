"""Filled-in profiles for the demo's lab heads: an overview, interests, earlier positions,
education, honors, service, teaching, talks, links, and featured papers, all invented, so that
every profile screen has something to show. Built from each person's research area, with a
random generator seeded per person, so the demo is the same every time."""

from __future__ import annotations

import random
import zlib
from typing import Any

from sqlalchemy.engine import Engine

from tessera.demo.world import Person, World
from tessera.profiles import store

UNIVERSITIES = (
    ("University of Northbridge", "Northbridge"),
    ("Halden State University", "Port Halden"),
    ("Westmere College of Medicine", "Westmere"),
    ("Larkspur University", "Larkspur"),
    ("Coastline Institute of Technology", "Brightwater"),
    ("Fernhill University", "Fernhill"),
    ("Riverbend School of Medicine", "Riverbend"),
)
SOCIETIES = {
    "immunology": "Northbridge Society for Immunology",
    "oncology": "Society for Translational Cancer Research",
    "neuroscience": "Larkspur Neuroscience Society",
    "structural": "Society for Molecular Structure",
    "computational": "Society for Computational Biology and Genomics",
    "population": "Coastline Society for Population Health",
}
DEFAULT_SOCIETY = "Corvane Research Society"
TALK_EVENTS = (
    "Annual Meeting of the {society}",
    "Larkspur Symposium on {topic}",
    "Westmere Conference on {topic}",
    "Halden Winter Workshop on {topic}",
)


def _rng(person: Person) -> random.Random:
    return random.Random(zlib.crc32(person.key.encode()))


SMALL = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "the", "to", "with"}
FIELDS = {
    "immunology": "Immunology",
    "oncology": "Cancer biology",
    "neuroscience": "Neuroscience",
    "structural": "Structural biology",
    "computational": "Computational biology",
    "population": "Epidemiology",
}


def _title(text: str) -> str:
    """Sentence case: the first letter up, the rest as written."""
    return text[:1].upper() + text[1:]


def _the(name: str) -> str:
    """'the University of Northbridge', but 'Larkspur University'."""
    return f"the {name}" if name.startswith("University") else name


def _heading(text: str) -> str:
    """Title case for names of events and lectures; words with capitals (TCF1) stay as written."""
    return " ".join(
        w if any(ch.isupper() for ch in w) or (i and w in SMALL) else w[:1].upper() + w[1:]
        for i, w in enumerate(text.split())
    )


def profile_for(
    person: Person, papers: list[str], cited: dict[str, int], today_year: int
) -> list[tuple[str, dict[str, Any]]]:
    """(section, fields) for one lab head, in the order they would enter them."""
    rng = _rng(person)
    area = person.area
    name = f"{person.given} {person.family}"
    processes = rng.sample(list(area.processes), k=min(3, len(area.processes)))
    models = rng.sample(list(area.models), k=min(2, len(area.models)))
    method = rng.choice(area.methods)
    society = SOCIETIES.get(area.key, DEFAULT_SOCIETY)
    phd_year = today_year - rng.randint(14, 24)
    postdoc_end = phd_year + rng.randint(4, 6)
    phd_school, phd_city = rng.choice(UNIVERSITIES)
    postdoc_school, postdoc_city = rng.choice([u for u in UNIVERSITIES if u[0] != phd_school])
    first_job_end = postdoc_end + rng.randint(4, 7)
    out: list[tuple[str, dict[str, Any]]] = [
        (
            "overview",
            {
                "text": (
                    f"{name} leads the {person.family} Lab in the {area.division}. "
                    f"The lab asks how {processes[0]} and {processes[1]} work, and why they "
                    f"sometimes fail, studying them in {models[0]} and {models[-1]} with "
                    f"{method.lower()}.\n\n{person.family} trained at {_the(phd_school)} and "
                    f"{_the(postdoc_school)} before joining the institute, and mentors "
                    f"students and fellows across the {area.division}."
                )
            },
        ),
        (
            "interests",
            {
                "text": _title(", ".join(processes))
                + "; "
                + ", ".join(models)
                + "; "
                + method.lower()
                + "."
            },
        ),
        (
            "education",
            {
                "institution": phd_school,
                "degree": "PhD",
                "field": rng.choice(
                    (
                        "Molecular biology",
                        "Biochemistry",
                        "Genetics",
                        FIELDS.get(area.key, "Biology"),
                    )
                ),
                "location": phd_city,
                "start": str(phd_year - 5),
                "end": str(phd_year),
            },
        ),
        (
            "education",
            {
                "institution": postdoc_school,
                "degree": "Postdoctoral fellowship",
                "field": FIELDS.get(area.key, "Biology"),
                "location": postdoc_city,
                "start": str(phd_year),
                "end": str(postdoc_end),
            },
        ),
        (
            "position",
            {
                "title": "Assistant Professor",
                "organization": postdoc_school,
                "department": area.division.replace("Division of", "Department of"),
                "location": postdoc_city,
                "start": str(postdoc_end),
                "end": str(first_job_end),
            },
        ),
        (
            "honor",
            {
                "title": "Early Career Investigator Award",
                "organization": society,
                "year": str(postdoc_end + rng.randint(1, 3)),
            },
        ),
        (
            "honor",
            {
                "title": f"Distinguished Lecture in {_heading(processes[0])}",
                "organization": rng.choice(UNIVERSITIES)[0],
                "year": str(today_year - rng.randint(0, 3)),
            },
        ),
        ("membership", {"role": "Member", "organization": society, "start": str(phd_year)}),
        (
            "membership",
            {
                "role": "Editorial board member",
                "organization": rng.choice(area.journals),
                "start": str(today_year - rng.randint(2, 6)),
            },
        ),
        (
            "membership",
            {
                "role": "Grant review panel member",
                "organization": "Corvane Institute pilot awards",
                "start": str(today_year - 2),
                "end": str(today_year - 1),
            },
        ),
        (
            "teaching",
            {
                "title": f"Topics in {_heading(processes[1])}",
                "kind": "course",
                "role": "Course director",
                "organization": "Corvane graduate program",
                "start": str(first_job_end + 1),
            },
        ),
        (
            "teaching",
            {
                "title": "Two doctoral students and three postdoctoral fellows",
                "kind": "mentee",
                "role": "Primary mentor",
                "organization": "Corvane Institute",
                "start": str(first_job_end),
            },
        ),
        (
            "talk",
            {
                "title": f"{_title(processes[0])} in {models[0]}",
                "kind": "invited",
                "event": rng.choice(TALK_EVENTS).format(
                    society=society, topic=_heading(processes[1])
                ),
                "location": rng.choice(UNIVERSITIES)[1],
                "date": f"{today_year - 1}-{rng.randint(1, 12):02d}",
            },
        ),
        (
            "talk",
            {
                "title": f"New tools for {processes[1]}",
                "kind": "keynote",
                "event": f"Annual Meeting of the {society}",
                "location": rng.choice(UNIVERSITIES)[1],
                "date": f"{today_year}-{rng.randint(1, 9):02d}",
            },
        ),
        (
            "link",
            {
                "label": "Lab website",
                "url": f"https://www.corvane.example/labs/{person.family.lower()}",
            },
        ),
    ]
    for pmid in sorted(papers, key=lambda p: (-cited.get(p, 0), p))[:2]:
        out.append(("featured", {"pmid": pmid}))
    return out


def build_profiles(
    engine: Engine, world: World, key_of: dict[str, str], today_year: int, limit: int = 12
) -> int:
    """Profiles for up to ``limit`` lab heads; returns how many entries were made."""
    by_key = {p.key: p for p in world.people}
    cited = {str(r.get("pmid")): int(r.get("citation_count") or 0) for r in world.icite}
    heads = [(pid, by_key[k]) for pid, k in key_of.items() if k in by_key and by_key[k].is_pi]
    with engine.connect() as conn:
        papers_of = {pid: sorted(store.confirmed_papers(conn, pid)) for pid, _ in heads}
    # Those with the most confirmed papers, so their featured papers have something to choose.
    heads.sort(key=lambda x: (-len(papers_of[x[0]]), x[1].family, x[0]))
    made = 0
    for pid, person in heads[:limit]:
        papers = papers_of[pid]
        actor = " ".join(x for x in (person.given, person.middle, person.family) if x)
        for section, fields in profile_for(person, papers, cited, today_year):
            store.add(engine, pid, section, fields, actor=actor, capacity="self")
            made += 1
    return made


__all__ = ["build_profiles", "profile_for"]
