"""Grant-to-impact threads (INT-08): what followed a grant, one typed and sourced step at a time.

::

    grant ── acknowledged by ──▶ papers ── referenced by ──▶ registered studies
      │                            └────── cited by ──▶ clinical articles
      └──────── named as funding by ──▶ registered studies

Every step is a link Tessera already keeps, with its source: ``acknowledges`` (NIH RePORTER's
link table, or the paper's own PubMed grant list), ``references`` (a registry record lists the
paper), ``funded_by`` (a registry record names the grant), and iCite's list of the clinical
articles that cite a paper. Only accepted links that their source still reports are followed,
so a link a curator rejected never appears. Retracted papers, and retracted citing articles,
are left out and counted as left out.

A thread shows association, never causation: these records came after the grant and are
connected to it as shown, which is not to say the grant caused them.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pyarrow as pa
from sqlalchemy import select
from sqlalchemy.engine import Engine

from tessera.lake import Lake
from tessera.ledger.tables import assertion

CHUNK = 500

# How a clinical article is described, most specific first (from its PubMed publication types).
CLINICAL_KINDS = {
    "guideline": "Practice guideline",
    "trial": "Clinical trial",
    "meta_analysis": "Meta-analysis or systematic review",
    "review": "Review",
    "clinical": "Other clinical article",
}
KIND_ORDER = list(CLINICAL_KINDS)
GUIDELINE_TYPES = frozenset(
    {"Practice Guideline", "Guideline", "Consensus Development Conference",
     "Consensus Development Conference, NIH"}
)  # fmt: skip
TRIAL_TYPES = frozenset(
    {"Randomized Controlled Trial", "Controlled Clinical Trial", "Pragmatic Clinical Trial",
     "Equivalence Trial", "Adaptive Clinical Trial"}
)  # fmt: skip
META_TYPES = frozenset({"Meta-Analysis", "Systematic Review"})

# Evidence features of the links a thread follows, named by where they came from.
SOURCE_OF = {
    "reporter_link": "NIH RePORTER",
    "pubmed_grant_list": "PubMed",
    "registry_reference": "ClinicalTrials.gov",
    "registry_secondary_id": "ClinicalTrials.gov",
}

ASSOCIATION = (
    "A thread shows what followed and how each step is linked. It is association, not "
    "causation: it does not say that the grant caused any of it."
)
CLINICAL_NOTE = (
    "Clinical articles are the articles NIH's iCite classifies as clinical (guidelines, trials, "
    "and other clinical research) that cite the papers. A clinical citation shows that clinical "
    "research or guidance draws on a paper; it is not evidence of patient benefit."
)


def clinical_kind(publication_types: Iterable[str] | None) -> str:
    """What kind of clinical article this is, from its PubMed publication types."""
    types = set(publication_types or ())
    if types & GUIDELINE_TYPES:
        return "guideline"
    if types & TRIAL_TYPES or any(t.startswith("Clinical Trial") for t in types):
        return "trial"
    if types & META_TYPES:
        return "meta_analysis"
    if "Review" in types:
        return "review"
    return "clinical"


# --- reading -----------------------------------------------------------------------------------


def _accepted(
    engine: Engine,
    predicate: str,
    *,
    subjects: Iterable[str] | None = None,
    objects: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Accepted links of one predicate that their source still reports."""
    column = assertion.c.subject_id if subjects is not None else assertion.c.object_id
    values = sorted(set(subjects if subjects is not None else objects or ()))
    out: list[dict[str, Any]] = []
    with engine.connect() as conn:
        for i in range(0, len(values), CHUNK):
            rows = conn.execute(
                select(assertion.c.id, assertion.c.subject_id, assertion.c.object_id,
                       assertion.c.evidence, assertion.c.qualifiers)
                .where(
                    assertion.c.predicate == predicate,
                    column.in_(values[i : i + CHUNK]),
                    assertion.c.status == "accepted",
                    assertion.c.reported.is_(True),
                )
            ).mappings()  # fmt: skip
            out.extend(dict(r) for r in rows)
    return sorted(out, key=lambda r: r["id"])


def _sources(row: dict[str, Any] | None) -> list[str]:
    labels: list[str] = []
    for item in (row or {}).get("evidence") or []:
        label = SOURCE_OF.get(item.get("feature"))
        if label and label not in labels:
            labels.append(label)
    return labels


def _join(lake: Lake, sql: str, keys: Iterable[str]) -> list[dict[str, Any]]:
    """Run ``sql`` with ``_keys(key)`` holding the given values."""
    values = sorted(set(keys))
    if not values:
        return []
    cur = lake.cursor()
    try:
        cur.register("_keys", pa.table({"key": pa.array(values, pa.string())}))
        return cur.sql(sql).to_arrow_table().to_pylist()
    finally:
        cur.close()


PAPERS_SQL = (
    "SELECT a.pmid, a.title, a.journal, a.pub_year AS year, "
    "coalesce(a.is_retracted, false) AS retracted FROM pubmed_article a "
    "JOIN _keys k ON k.key = a.pmid"
)
STUDIES_SQL = (
    "SELECT s.nct_id, s.brief_title AS title, s.overall_status AS status, s.phases, "
    "s.study_type, year(s.start_date) AS start_year FROM ctgov_study s "
    "JOIN _keys k ON k.key = s.nct_id"
)
CITATIONS_SQL = (
    "SELECT c.pmid, c.citing_pmid FROM icite_clinical_citation c JOIN _keys k ON k.key = c.pmid"
)
CITING_SQL = (
    "SELECT c.pmid, c.title, c.journal, c.pub_year AS year, c.publication_types AS types, "
    "c.first_author, coalesce(c.is_retracted, false) AS retracted FROM citing_article c "
    "JOIN _keys k ON k.key = c.pmid"
)
# A citing article that is also one of the organization's papers is read from there.
CITING_OURS_SQL = (
    "SELECT a.pmid, a.title, a.journal, a.pub_year AS year, a.publication_types AS types, "
    "(SELECT coalesce(u.collective_name, trim(coalesce(u.family, '') || ' ' || "
    "coalesce(u.initials, ''))) FROM pubmed_author u WHERE u.pmid = a.pmid AND u.position = 1) "
    "AS first_author, coalesce(a.is_retracted, false) AS retracted FROM pubmed_article a "
    "JOIN _keys k ON k.key = a.pmid"
)


def citing_articles(lake: Lake, pmids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Each citing article's record, with its kind (``known`` is false until it is fetched)."""
    wanted = sorted(set(pmids))
    found = {r["pmid"]: r for r in _join(lake, CITING_SQL, wanted)}
    found.update(
        {r["pmid"]: r for r in _join(lake, CITING_OURS_SQL, [p for p in wanted if p not in found])}
    )
    out = {}
    for pmid in wanted:
        r = found.get(pmid)
        kind = clinical_kind(r["types"] if r else None)
        out[pmid] = {
            "pmid": pmid,
            "title": r["title"] if r else None,
            "journal": r["journal"] if r else None,
            "year": r["year"] if r else None,
            "first_author": (r["first_author"] or None) if r else None,
            "kind": kind,
            "kind_label": CLINICAL_KINDS[kind],
            "retracted": bool(r["retracted"]) if r else False,
            "known": r is not None,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        }
    return out


# --- the graph ---------------------------------------------------------------------------------


@dataclass
class _Graph:
    """Everything that followed a set of papers (and, for grants, the studies naming them)."""

    papers: dict[str, dict[str, Any]] = field(default_factory=dict)
    links: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # pmid -> its links
    retracted: set[str] = field(default_factory=set)
    via: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    studies: dict[str, dict[str, Any]] = field(default_factory=dict)
    cited_by: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    citing: dict[str, dict[str, Any]] = field(default_factory=dict)
    retracted_citing: set[str] = field(default_factory=set)


def _graph(
    engine: Engine,
    lake: Lake,
    links: dict[str, list[dict[str, Any]]],
    funded: Iterable[dict[str, Any]] = (),
    *,
    keep_retracted: bool = False,
) -> _Graph:
    g = _Graph(links=links)
    meta = {r["pmid"]: r for r in _join(lake, PAPERS_SQL, links)}
    for pmid in links:
        paper = meta.get(pmid) or {"pmid": pmid, "title": None, "journal": None, "year": None,
                                   "retracted": False}  # fmt: skip
        if paper["retracted"] and not keep_retracted:
            g.retracted.add(pmid)
            continue
        g.papers[pmid] = paper
    for r in _accepted(engine, "references", objects=[f"pub:{p}" for p in g.papers]):
        g.via[r["subject_id"].removeprefix("stu:")].append(
            {"type": "references", "pmid": r["object_id"].removeprefix("pub:"),
             "reference_type": (r["qualifiers"] or {}).get("reference_type"),
             "assertion_id": r["id"], "sources": _sources(r)}
        )  # fmt: skip
    for r in funded:
        g.via[r["subject_id"].removeprefix("stu:")].append(
            {"type": "funded_by", "core": r["object_id"].removeprefix("grt:"),
             "assertion_id": r["id"], "sources": _sources(r)}
        )  # fmt: skip
    g.studies = {r["nct_id"]: r for r in _join(lake, STUDIES_SQL, g.via)}
    pairs = _join(lake, CITATIONS_SQL, g.papers)
    g.citing = citing_articles(lake, {r["citing_pmid"] for r in pairs})
    for r in pairs:
        if g.citing[r["citing_pmid"]]["retracted"]:
            g.retracted_citing.add(r["citing_pmid"])
            continue
        g.cited_by[r["pmid"]].add(r["citing_pmid"])
    return g


def _view(g: _Graph, pmids: Iterable[str], cores: Iterable[str] = ()) -> dict[str, Any]:
    """The part of the graph that follows these papers (and studies naming these grants)."""
    pmids = {p for p in pmids if p in g.papers}
    cores = set(cores)
    studies_of: dict[str, set[str]] = defaultdict(set)
    studies = []
    for nct, via in g.via.items():
        here = [v for v in via if v.get("pmid") in pmids or v.get("core") in cores]
        if not here:
            continue
        for v in here:
            if v.get("pmid"):
                studies_of[v["pmid"]].add(nct)
        s = g.studies.get(nct) or {"nct_id": nct, "title": None, "status": None, "phases": None,
                                   "study_type": None, "start_year": None}  # fmt: skip
        studies.append({**s, "id": f"stu:{nct}", "phases": s["phases"] or [], "via": here})
    studies.sort(key=lambda s: (-(s["start_year"] or 0), s["nct_id"]))

    cites: dict[str, set[str]] = defaultdict(set)
    for pmid in pmids:
        for citing in g.cited_by.get(pmid, ()):
            cites[citing].add(pmid)
    clinical = [{**g.citing[c], "cites": sorted(cites[c])} for c in cites]
    clinical.sort(key=lambda c: (KIND_ORDER.index(c["kind"]), -(c["year"] or 0), c["pmid"]))

    papers = []
    for pmid in pmids:
        p = g.papers[pmid]
        links = g.links.get(pmid) or []
        sources: list[str] = []
        for link in links:
            sources += [s for s in _sources(link) if s not in sources]
        papers.append({
            **p, "id": f"pub:{pmid}", "sources": sources,
            "assertion_ids": [link["id"] for link in links if link.get("id")],
            "studies": sorted(studies_of.get(pmid, ())),
            "clinical": sorted(g.cited_by.get(pmid, ()), key=int),
        })  # fmt: skip
    # Papers that something followed come first, then the rest, newest first.
    papers.sort(key=lambda p: (not (p["studies"] or p["clinical"]), -(p["year"] or 0), p["pmid"]))

    years = [c["year"] for c in clinical if c["year"]]
    summary = {
        "papers": len(papers),
        "studies": len(studies),
        "clinical": len(clinical),
        "guidelines": sum(1 for c in clinical if c["kind"] == "guideline"),
        "trials": sum(1 for c in clinical if c["kind"] == "trial"),
        "papers_cited_clinically": sum(1 for p in papers if p["clinical"]),
        "papers_referenced_by_studies": sum(1 for p in papers if p["studies"]),
        "first_clinical_year": min(years) if years else None,
        "awaiting_records": sum(1 for c in clinical if not c["known"]),
    }
    return {"papers": papers, "studies": studies, "clinical": clinical, "summary": summary}


def _notes(view: dict[str, Any], g: _Graph, today: date) -> list[str]:
    notes = [ASSOCIATION]
    if view["clinical"]:
        notes.append(CLINICAL_NOTE)
    recent = [p for p in view["papers"] if (p["year"] or 0) >= today.year - 1]
    if recent:
        notes.append(f"{len(recent):,} of the papers are from {today.year - 1} or later and have "
                     "had little time to be cited.")  # fmt: skip
    left = len(g.retracted)
    if left:
        notes.append(f"{left:,} retracted {'paper is' if left == 1 else 'papers are'} left out.")
    waiting = view["summary"]["awaiting_records"]
    if waiting:
        notes.append(f"{waiting:,} clinical {'article is' if waiting == 1 else 'articles are'} "
                     "shown by PubMed ID until the next sync fetches the record.")  # fmt: skip
    return notes


# --- threads -----------------------------------------------------------------------------------


def _grant_meta(lake: Lake, cores: Iterable[str]) -> dict[str, dict[str, Any]]:
    rows = _join(
        lake,
        "SELECT p.core_project_num AS core, p.fiscal_year, p.appl_id, p.title, p.activity_code, "
        "p.project_start, p.project_end, p.subproject_id, p.agency_ic_admin AS institute "
        "FROM reporter_project p JOIN _keys k ON k.key = p.core_project_num "
        "ORDER BY p.core_project_num, p.fiscal_year, p.appl_id",
        cores,
    )
    by_core: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_core[r["core"]].append(r)
    out = {}
    for core, years in by_core.items():
        parent = [y for y in years if not y["subproject_id"]] or years
        latest = parent[-1]
        start = latest["project_start"]
        out[core] = {
            "id": f"grt:{core}",
            "core_project_num": core,
            "title": latest["title"],
            "activity_code": latest["activity_code"],
            "institute": latest["institute"],
            "project_start": start,
            "project_end": latest["project_end"],
            "first_year": start.year if start else min(y["fiscal_year"] for y in years),
        }
    return out


def grant_thread(
    engine: Engine, lake: Lake, core: str, *, today: date | None = None
) -> dict[str, Any] | None:
    """Everything that followed one grant, or None when there is no such grant."""
    core = core.upper()
    grant = _grant_meta(lake, [core]).get(core)
    if grant is None:
        return None
    ref = f"grt:{core}"
    links: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in _accepted(engine, "acknowledges", objects=[ref]):
        links[a["subject_id"].removeprefix("pub:")].append(a)
    funded = _accepted(engine, "funded_by", objects=[ref])
    g = _graph(engine, lake, dict(links), funded)
    view = _view(g, g.papers, [core])
    first = view["summary"]["first_clinical_year"]
    view["summary"]["years_to_first_clinical"] = (
        first - grant["first_year"] if first and grant["first_year"] else None
    )
    view["summary"]["retracted_left_out"] = len(g.retracted)
    return {"grant": grant, **view, "notes": _notes(view, g, today or date.today())}


def paper_thread(
    engine: Engine, lake: Lake, pmid: str, *, today: date | None = None
) -> dict[str, Any] | None:
    """The grants a paper acknowledges, and what followed the paper; None if it isn't kept."""
    acks = _accepted(engine, "acknowledges", subjects=[f"pub:{pmid}"])
    g = _graph(engine, lake, {pmid: []}, keep_retracted=True)
    if g.papers[pmid]["title"] is None and g.papers[pmid]["year"] is None:
        return None
    view = _view(g, [pmid])
    grants_meta = _grant_meta(lake, [a["object_id"].removeprefix("grt:") for a in acks])
    grants = [
        {**grants_meta.get(core, {"id": f"grt:{core}", "core_project_num": core}),
         "assertion_id": a["id"], "sources": _sources(a)}
        for a in acks
        for core in [a["object_id"].removeprefix("grt:")]
    ]  # fmt: skip
    grants.sort(key=lambda x: x["core_project_num"])
    return {"paper": view["papers"][0], "grants": grants, "studies": view["studies"],
            "clinical": view["clinical"], "summary": view["summary"],
            "notes": _notes(view, g, today or date.today())}  # fmt: skip


def grant_impacts(engine: Engine, lake: Lake, cores: Iterable[str]) -> dict[str, dict[str, Any]]:
    """For each grant, what followed it in counts (for lists of grants)."""
    cores = sorted({c.upper() for c in cores})
    meta = _grant_meta(lake, cores)
    refs = [f"grt:{c}" for c in cores]
    links: dict[str, list[dict[str, Any]]] = defaultdict(list)
    papers_of: dict[str, set[str]] = defaultdict(set)
    for a in _accepted(engine, "acknowledges", objects=refs):
        core, pmid = a["object_id"].removeprefix("grt:"), a["subject_id"].removeprefix("pub:")
        links[pmid].append(a)
        papers_of[core].add(pmid)
    funded = _accepted(engine, "funded_by", objects=refs)
    g = _graph(engine, lake, dict(links), funded)
    out = {}
    for core in cores:
        if core not in meta:
            continue
        view = _view(g, papers_of.get(core, ()), [core])
        out[core] = {**meta[core], "summary": view["summary"]}
    return out


def person_impact(
    engine: Engine, lake: Lake, person_id: str, *, today: date | None = None
) -> dict[str, Any]:
    """What followed a person's grants, and their own confirmed papers."""
    cores = [
        a["object_id"].removeprefix("grt:")
        for a in _accepted(engine, "investigator_on", subjects=[person_id])
    ]
    grants = sorted(
        grant_impacts(engine, lake, cores).values(),
        key=lambda x: (-(x["first_year"] or 0), x["core_project_num"]),
    )
    authored = _accepted(engine, "authored", subjects=[person_id])
    links = {a["object_id"].removeprefix("pub:"): [] for a in authored}
    g = _graph(engine, lake, links)
    view = _view(g, g.papers)
    return {
        "grants": grants,
        "papers": view["summary"],
        "clinical": view["clinical"][:50],
        "notes": _notes(view, g, today or date.today()),
    }


__all__ = [
    "ASSOCIATION",
    "CLINICAL_KINDS",
    "CLINICAL_NOTE",
    "citing_articles",
    "clinical_kind",
    "grant_impacts",
    "grant_thread",
    "paper_thread",
    "person_impact",
]
