"""The organization's vocabulary: the MeSH headings its people hold, with their synonyms and
their place in the hierarchy, and each person's broader research areas.

Built with the concept profiles, from the ``mesh_descriptor`` table (NLM's MeSH, read once a
year). Only the headings that matter are kept: those in someone's profile and every heading
above them, so lookups when someone searches stay small and fast. Without MeSH data the
vocabulary still lists the profiles' headings, with no synonyms or hierarchy, and everything
that uses it degrades to what it did before.

* **Synonyms:** each heading's entry terms ("Cancer" for "Neoplasms").
* **Hierarchy:** tree numbers place a heading under broader ones (``C04.588.180`` is under
  ``C04.588``, which is under ``C04``). ``people_below`` counts the people who hold a heading
  or any heading beneath it, so a broad heading has a meaningful weight in a search.
* **Research areas:** a person's concept weights rolled up to the top of each tree branch
  (``C04`` Neoplasms, ``A15`` Hemic and Immune Systems), weighed by how few people share the
  branch (as topics are), as shares of their profile. Only
  branches that say what someone studies count: anatomy, organisms, diseases, psychology,
  and phenomena and processes; molecules and methods remain topics.
"""

from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import pyarrow as pa

from tessera.analytics.concepts import _write
from tessera.home import DataHome
from tessera.lake import Lake

VOCABULARY_FILE = "concept_vocabulary.parquet"
AREA_FILE = "concept_area.parquet"
AREAS_KEPT = 8
# Research areas name what someone studies: a disease, a body system, an organism, a process.
# Molecules (D), methods (E), and the other branches stay visible as topics, not areas.
AREA_CATEGORIES = ("A", "B", "C", "F", "G")

CATEGORIES = {
    "A": "Anatomy",
    "B": "Organisms",
    "C": "Diseases",
    "D": "Chemicals and Drugs",
    "E": "Techniques and Equipment",
    "F": "Psychiatry and Psychology",
    "G": "Phenomena and Processes",
    "H": "Disciplines and Occupations",
    "I": "Anthropology, Education, and Society",
    "J": "Technology, Industry, and Agriculture",
    "K": "Humanities",
    "L": "Information Science",
    "M": "Named Groups",
    "N": "Health Care",
    "V": "Publication Characteristics",
    "Z": "Geographicals",
}

VOCABULARY_SCHEMA = pa.schema(
    [
        ("descriptor_ui", pa.string()),
        ("name", pa.string()),
        ("tree_numbers", pa.list_(pa.string())),
        ("entry_terms", pa.list_(pa.string())),
        ("category", pa.string()),
        ("people", pa.int64()),  # people who hold this heading
        ("people_below", pa.int64()),  # people who hold it or any heading beneath it
        ("mesh_year", pa.int16()),
    ]
)


def vocabulary_path(home: DataHome) -> Path:
    return home.derived / VOCABULARY_FILE


def area_path(home: DataHome) -> Path:
    return home.derived / AREA_FILE


def ancestors(tree: str) -> list[str]:
    """``C04.588.180`` -> [``C04``, ``C04.588``]."""
    parts = tree.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts))]


def _latest_mesh(lake: Lake) -> list[dict[str, Any]]:
    cur = lake.cursor()
    try:
        result = cur.execute(
            "SELECT descriptor_ui, name, tree_numbers, entry_terms, mesh_year "
            "FROM mesh_descriptor WHERE mesh_year = (SELECT max(mesh_year) FROM mesh_descriptor)"
        )
        names = [d[0] for d in result.description]
        return [dict(zip(names, row, strict=True)) for row in result.fetchall()]
    finally:
        cur.close()


def build_vocabulary(home: DataHome, profile: pa.Table) -> dict[str, Any]:
    """Write ``derived/concept_vocabulary.parquet`` and ``derived/concept_area.parquet``."""
    held: dict[str, set[str]] = defaultdict(set)
    names: dict[str, str] = {}
    for row in profile.select(["person_id", "descriptor_ui", "descriptor"]).to_pylist():
        held[row["descriptor_ui"]].add(row["person_id"])
        names[row["descriptor_ui"]] = row["descriptor"]
    with Lake(home.lake) as lake:
        mesh = _latest_mesh(lake)
    by_ui = {r["descriptor_ui"]: r for r in mesh}
    by_tree = {t: r["descriptor_ui"] for r in mesh for t in r["tree_numbers"] or []}
    year = mesh[0]["mesh_year"] if mesh else None

    # The headings that matter: those people hold, and every heading above them.
    keep = set(held)
    for ui in held:
        for tree in (by_ui.get(ui) or {}).get("tree_numbers") or []:
            keep.update(by_tree[a] for a in ancestors(tree) if a in by_tree)
    # Headings left out of profiles (check tags, laboratory tools) stay out here too, even
    # as parents of headings people hold.
    from tessera.analytics.concepts import TOOLS
    from tessera.evidence.authorship import CHECK_TAGS

    excluded = CHECK_TAGS | TOOLS
    keep = {ui for ui in keep if (by_ui.get(ui) or {}).get("name", names.get(ui)) not in excluded}
    below: dict[str, set[str]] = defaultdict(set)
    for ui, people in held.items():
        below[ui] |= people
        for tree in (by_ui.get(ui) or {}).get("tree_numbers") or []:
            for a in ancestors(tree):
                if a in by_tree:
                    below[by_tree[a]] |= people
    rows: dict[str, list[Any]] = {name: [] for name in VOCABULARY_SCHEMA.names}
    for ui in sorted(keep):
        record = by_ui.get(ui, {})
        trees = sorted(record.get("tree_numbers") or [])
        rows["descriptor_ui"].append(ui)
        rows["name"].append(record.get("name") or names.get(ui, ui))
        rows["tree_numbers"].append(trees)
        rows["entry_terms"].append(list(record.get("entry_terms") or []))
        rows["category"].append(trees[0][0] if trees else None)
        rows["people"].append(len(held.get(ui, ())))
        rows["people_below"].append(len(below.get(ui, held.get(ui, set()))))
        rows["mesh_year"].append(year)
    vocabulary = pa.table(rows, schema=VOCABULARY_SCHEMA)
    areas = _areas(profile, by_ui, by_tree)
    home.derived.mkdir(parents=True, exist_ok=True)
    _write(vocabulary, vocabulary_path(home))
    _write(areas, area_path(home))
    return {"vocabulary": vocabulary.num_rows, "areas": areas.num_rows, "mesh_year": year}


AREA_SCHEMA = pa.schema(
    [
        ("person_id", pa.string()),
        ("area_tree", pa.string()),
        ("area_ui", pa.string()),
        ("area", pa.string()),
        ("category", pa.string()),
        ("share", pa.float64()),
        ("concepts", pa.list_(pa.string())),  # the person's headings behind it, strongest first
    ]
)


def _areas(
    profile: pa.Table, by_ui: dict[str, dict[str, Any]], by_tree: dict[str, str]
) -> pa.Table:
    """Each person's profile rolled up to the top of each tree branch, as shares."""
    rows: dict[str, list[Any]] = {name: [] for name in AREA_SCHEMA.names}
    if not by_ui:
        return pa.table(rows, schema=AREA_SCHEMA)
    per_person: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    behind: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in profile.select(["person_id", "descriptor_ui", "descriptor", "weight"]).to_pylist():
        trees = [
            t
            for t in (by_ui.get(r["descriptor_ui"]) or {}).get("tree_numbers") or []
            if t[0] in AREA_CATEGORIES
        ]
        tops = sorted({t.split(".")[0] for t in trees})
        for top in tops:  # a heading under several branches counts a part toward each
            part = r["weight"] / len(tops)
            per_person[r["person_id"]][top] += part
            behind[(r["person_id"], top)][r["descriptor"]] += part
    # Like topics, areas that nearly everyone shares ("Cells") say little: weigh by IDF.
    people = len(per_person) or 1
    holders: dict[str, int] = defaultdict(int)
    for areas in per_person.values():
        for top in areas:
            holders[top] += 1
    for person, raw in sorted(per_person.items()):
        areas = {top: w * math.log((people + 1) / holders[top]) for top, w in raw.items()}
        areas = {top: w for top, w in areas.items() if w > 0}
        total = sum(areas.values()) or 1.0
        for top, weight in sorted(areas.items(), key=lambda kv: (-kv[1], kv[0]))[:AREAS_KEPT]:
            ui = by_tree.get(top)
            rows["person_id"].append(person)
            rows["area_tree"].append(top)
            rows["area_ui"].append(ui)
            rows["area"].append(by_ui[ui]["name"] if ui in by_ui else top)
            rows["category"].append(top[0])
            rows["share"].append(round(weight / total, 4))
            concepts = behind[(person, top)]
            rows["concepts"].append(sorted(concepts, key=lambda c: -concepts[c])[:4])
    return pa.table(rows, schema=AREA_SCHEMA)


def vocabulary_rows(lake: Lake, home: DataHome) -> list[dict[str, Any]]:
    path = vocabulary_path(home)
    if not path.is_file():
        return []
    cur = lake.cursor()
    try:
        result = cur.execute("SELECT * FROM read_parquet(?)", [str(path)])
        names = [d[0] for d in result.description]
        return [dict(zip(names, row, strict=True)) for row in result.fetchall()]
    finally:
        cur.close()


def person_areas(lake: Lake, home: DataHome, person_id: str) -> list[dict[str, Any]]:
    path = area_path(home)
    if not path.is_file():
        return []
    cur = lake.cursor()
    try:
        result = cur.execute(
            "SELECT area_tree, area_ui, area, category, share, concepts FROM read_parquet(?) "
            "WHERE person_id = ? ORDER BY share DESC",
            [str(path), person_id],
        )
        names = [d[0] for d in result.description]
        return [dict(zip(names, row, strict=True)) for row in result.fetchall()]
    finally:
        cur.close()


def mesh_year(home: DataHome) -> int | None:
    """The MeSH year behind the vocabulary, for the credit NLM asks for (None without MeSH)."""
    path = vocabulary_path(home)
    if not path.is_file():
        return None
    import pyarrow.parquet as pq

    years = pq.read_table(path, columns=["mesh_year"]).column("mesh_year").to_pylist()
    return next((y for y in years if y), None)


__all__ = [
    "CATEGORIES",
    "ancestors",
    "area_path",
    "build_vocabulary",
    "mesh_year",
    "person_areas",
    "vocabulary_path",
    "vocabulary_rows",
]
