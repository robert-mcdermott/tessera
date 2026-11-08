"""The demo organization's groups, as its own registry would export them: divisions, a lab for
every principal investigator, programs that span divisions, and a shared resource, with
everyone's role and dates. Imported through ``tessera.groups.importer`` like any feed, under
the source ``registry``.

Everything here is invented, like the rest of the demo.
"""

from __future__ import annotations

import random
from datetime import date
from typing import Any

from tessera.demo.world import DOMAIN, World
from tessera.groups.store import slugify

DIVISIONS = {
    "Division of Immunology and Infection": (
        "How the immune system remembers, how pathogens evade it, and how vaccines and "
        "antivirals can tip the balance."
    ),
    "Division of Molecular Oncology": (
        "The genomes and microenvironments of tumors, and the cell therapies and precision "
        "medicines that follow from them."
    ),
    "Division of Neuroscience": (
        "Circuits, memory, sleep, and the neurodegenerative diseases that disturb them."
    ),
    "Division of Structural and Computational Biology": (
        "Molecular machines seen atom by atom, and the algorithms that find patterns in "
        "biological data."
    ),
    "Division of Population Health": (
        "Cancer screening, prevention, and health equity, studied in the communities the "
        "Institute serves."
    ),
}
LAB_FOCUS = {
    "immunology": "memory lymphocytes and mucosal immunity",
    "infection": "viral persistence, vaccines, and antiviral resistance",
    "genomics": "tumor genomes, DNA repair, and clonal evolution",
    "immunotherapy": "engineered T cells and the tumor microenvironment",
    "neuroscience": "synapses, memory, and neurodegeneration",
    "structural": "membrane proteins and cryo-electron microscopy",
    "computational": "single-cell data, gene regulation, and machine learning",
    "population": "screening, risk factors, and health equity",
}
# Programs span divisions: (name, short name, areas, description).
PROGRAMS = (
    (
        "Cancer Immunotherapy Program",
        "CIP",
        ("immunotherapy", "immunology"),
        "Immunologists and oncologists building and testing cell therapies together.",
    ),
    (
        "Brain Health and Aging Program",
        "BHAP",
        ("neuroscience", "population"),
        "From synapses to populations: why some brains age well, and how to help the rest.",
    ),
    (
        "Computational Oncology Program",
        "COP",
        ("computational", "genomics"),
        "Methods and models for reading tumor genomes at single-cell resolution.",
    ),
)
TITLE_ROLE = {
    "Postdoctoral Fellow": "trainee",
    "Staff Scientist": "staff",
    "Senior Research Scientist": "staff",
    "Research Associate": "staff",
}


def registry(world: World, today: date, seed: int = 11) -> dict[str, list[dict[str, Any]]]:
    """Rows for ``import_groups``: {"groups": [...], "memberships": [...]}."""
    rng = random.Random(seed)
    people = [p for p in world.people if p.on_roster]
    pis = [p for p in people if p.is_pi]
    start = {r["email"]: r["start"] for r in world.roster}
    first_year = today.year - 4
    groups: list[dict[str, Any]] = []
    memberships: list[dict[str, Any]] = []
    division_id = {name: f"D{i + 1}" for i, name in enumerate(DIVISIONS)}
    for name, text in DIVISIONS.items():
        groups.append(
            {
                "id": division_id[name],
                "name": name,
                "kind": "division",
                "description": text,
                "email": f"{slugify(name).removeprefix('division-of-')}@{DOMAIN}",
                "website": f"https://www.{DOMAIN}/divisions/{slugify(name)}",
            }
        )
    lab_id: dict[str, str] = {}
    for i, pi in enumerate(pis):
        lab_id[pi.key] = f"L{100 + i}"
        groups.append(
            {
                "id": lab_id[pi.key],
                "name": f"{pi.family} Lab",
                "kind": "lab",
                "parents": [division_id[pi.area.division]],
                "description": f"The {pi.family} Lab studies {LAB_FOCUS[pi.area.key]}.",
                "email": f"{slugify(pi.family)}-lab@{DOMAIN}",
                "website": f"https://www.{DOMAIN}/labs/{slugify(pi.family)}",
            }
        )
    for i, (name, short, areas, text) in enumerate(PROGRAMS):
        divisions = sorted({division_id[p.area.division] for p in pis if p.area.key in areas})
        groups.append(
            {
                "id": f"P{i + 1}",
                "name": name,
                "short_name": short,
                "kind": "program",
                "parents": divisions,
                "description": text,
                "email": f"{short.lower()}@{DOMAIN}",
            }
        )
        leaders = [next(p for p in pis if p.area.key == area) for area in areas]
        for j, pi in enumerate(p for p in pis if p.area.key in areas):
            memberships.append(
                {
                    "id": f"P{i + 1}-{pi.key}",
                    "group": f"P{i + 1}",
                    "email": pi.email,
                    "role": ("lead" if pi is leaders[0] else "co_lead")
                    if pi in leaders
                    else "member",
                    "start": str(first_year - 2 + (j % 3)),
                }
            )
    structural = [p for p in pis if p.area.key == "structural"]
    if structural:
        groups.append(
            {
                "id": "C1",
                "name": "Cryo-EM Shared Resource",
                "kind": "core",
                "parents": [division_id[structural[0].area.division]],
                "description": "Microscopes, training, and data processing for cryo-electron "
                "microscopy, open to every lab at the Institute.",
                "email": f"cryoem@{DOMAIN}",
            }
        )
        memberships.append(
            {"id": "C1-lead", "group": "C1", "email": structural[0].email, "role": "lead",
             "start": str(first_year - 3)}
        )  # fmt: skip
        staff = next(
            (p for p in people if p.lab == structural[0].key and p.title == "Staff Scientist"),
            None,
        )
        if staff:
            memberships.append(
                {"id": "C1-staff", "group": "C1", "email": staff.email, "role": "staff",
                 "title": "Facility Manager", "start": str(first_year - 1)}
            )  # fmt: skip
    for p in people:
        role = "lead" if p.is_pi else TITLE_ROLE.get(p.title, "member")
        memberships.append(
            {
                "id": f"{lab_id[p.lab]}-{p.key}",
                "group": lab_id[p.lab],
                "email": p.email,
                "role": role,
                "title": p.title,
                "start": start.get(p.email, str(first_year)),
            }
        )
    # Alumni: a few people trained in another lab of their field before joining this one.
    movers = rng.sample(
        sorted((p for p in people if not p.is_pi), key=lambda p: p.key), min(6, len(people))
    )
    for p in movers:
        others = [pi for pi in pis if pi.area.key == p.area.key and pi.key != p.lab]
        if not others:
            continue
        joined = int(start.get(p.email, str(first_year)))
        memberships.append(
            {
                "id": f"{lab_id[others[0].key]}-{p.key}-past",
                "group": lab_id[others[0].key],
                "email": p.email,
                "role": "trainee",
                "title": "Postdoctoral Fellow",
                "start": str(joined - 4),
                "end": str(joined - 1),
            }
        )
    return {"groups": groups, "memberships": memberships}


__all__ = ["registry"]
