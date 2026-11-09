"""Impact figures (INT-08): what followed a cohort's papers, for any cohort and period."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from tessera.impact.threads import CLINICAL_KINDS
from tessera.insight.figure import Column, Figure, FigureRequest, share
from tessera.insight.inputs import Inputs
from tessera.insight.metrics import BASIS, PAPER_RULE, WINDOW_RULE, Metric, register
from tessera.insight.publications import (
    PAPER_RECORD_COLUMNS,
    PUBMED_CAVEAT,
    basis_notes,
    counted_papers,
    paper_records,
)

ASSOCIATION_CAVEAT = (
    "These figures show what followed the papers and how it is linked to them. That is "
    "association, not causation, and it says nothing about the quality of anyone's work; "
    "Insight never ranks people by it."
)
CLINICAL_CAVEAT = (
    "A clinical citation shows that clinical research or guidance (as NIH's iCite classifies "
    "it) draws on a paper. It is not evidence of patient benefit, and recent papers have had "
    "little time to be cited."
)
KIND_NAMES = {  # singular, plural
    "guideline": ("practice guideline", "practice guidelines"),
    "trial": ("clinical trial", "clinical trials"),
    "meta_analysis": ("meta-analysis or systematic review", "meta-analyses or systematic reviews"),
    "review": ("review", "reviews"),
    "clinical": ("other clinical article", "other clinical articles"),
}
REGISTRY_CAVEAT = (
    "Registry records list the publications their sponsors choose to report, as background or "
    "as results; many studies list none, so a missing reference is not evidence of no use."
)


def _recent_note(request: FigureRequest) -> list[str]:
    recent = request.today.year - 1
    if request.period.end >= recent:
        return [f"Papers from {recent} on have had little time to be cited or referenced."]
    return []


def _clinical(inputs: Inputs, request: FigureRequest) -> Figure:
    basis = request.options["basis"]
    counted = counted_papers(inputs, request.period, basis)
    citing: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in inputs.rows("clinical_citations"):
        if r["pmid"] in counted:
            citing[r["pmid"]].append(r)
    rows = []
    for year in request.period.years:
        here = [c for c in counted.values() if c.year == year]
        cited = [c for c in here if citing.get(c.pmid)]
        guided = [c for c in cited if any(x["kind"] == "guideline" for x in citing[c.pmid])]
        articles = {x["citing_pmid"] for c in here for x in citing.get(c.pmid, [])}
        rows.append({"year": year, "papers": len(here), "cited": len(cited),
                     "guideline": len(guided), "articles": len(articles),
                     "share": share(len(cited), len(here))})  # fmt: skip
    records = paper_records(counted)
    for r in records:
        mine = sorted(citing.get(r["pmid"], []), key=lambda x: x["citing_pmid"])
        r["clinical_articles"] = len(mine)
        r["guidelines"] = sum(1 for x in mine if x["kind"] == "guideline")
        r["citing"] = [x["citing_pmid"] for x in mine]
    all_cited = sum(1 for c in counted if citing.get(c))
    all_guided = sum(1 for c in counted if any(x["kind"] == "guideline" for x in citing.get(c, [])))
    articles = {x["citing_pmid"] for c in counted for x in citing.get(c, [])}
    kinds: dict[str, int] = defaultdict(int)
    seen: set[str] = set()
    for c in counted:
        for x in citing.get(c, []):
            if x["citing_pmid"] not in seen:
                seen.add(x["citing_pmid"])
                kinds[x["kind"]] += 1
    notes = basis_notes(counted, basis)
    if counted:
        notes.append(
            f"{all_cited:,} of {len(counted):,} papers ({all_cited / len(counted):.0%}) are cited "
            f"by at least one clinical article; {all_guided:,} by a practice guideline."
        )
    if articles:
        found = [k for k in CLINICAL_KINDS if kinds.get(k)]
        parts = [f"{kinds[k]:,} {KIND_NAMES[k][kinds[k] != 1]}" for k in found]
        notes.append(f"The {len(articles):,} citing articles: {', '.join(parts)}.")
    notes += _recent_note(request)
    return Figure(
        request,
        [Column("year", "Year", "year"),
         Column("papers", "Papers", "count", "papers"),
         Column("cited", "Cited by clinical articles", "count", "papers"),
         Column("guideline", "Cited by a practice guideline", "count", "papers"),
         Column("share", "Share cited", "share"),
         Column("articles", "Clinical articles citing them", "count", "articles")],
        rows,
        totals={"papers": len(counted), "cited": all_cited, "guideline": all_guided,
                "articles": len(articles)},
        notes=notes,
        record_columns=[*PAPER_RECORD_COLUMNS,
                        Column("clinical_articles", "Clinical articles", "count", "articles"),
                        Column("guidelines", "Practice guidelines", "count", "articles"),
                        Column("citing", "Citing PubMed IDs", "list")],
        records=records,
    )  # fmt: skip


IMPACT_CLINICAL = register(
    Metric(
        id="impact.clinical_citations",
        version=1,
        name="Clinical citations",
        area="Impact",
        definition="How many of the cohort's papers in the period are cited by clinical "
        "articles (guidelines, trials, and other clinical research), and by practice guidelines.",
        counting=f"{PAPER_RULE} {WINDOW_RULE} A paper is cited clinically when NIH's iCite lists "
        "at least one clinical article citing it; a practice guideline is a citing article "
        "whose PubMed publication types include Practice Guideline, Guideline, or a consensus "
        "development conference. Retracted citing articles are not counted. Papers are counted "
        "in their year of publication, however late the citation came.",
        unit="papers",
        sources=("pubmed", "icite"),
        options=(BASIS,),
        caveats=(PUBMED_CAVEAT, CLINICAL_CAVEAT, ASSOCIATION_CAVEAT),
        needs=("paper_links", "papers", "clinical_citations"),
        compute=_clinical,
    )
)


def _studies(inputs: Inputs, request: FigureRequest) -> Figure:
    basis = request.options["basis"]
    counted = counted_papers(inputs, request.period, basis)
    studies_of: dict[str, set[str]] = defaultdict(set)
    for r in inputs.rows("study_references"):
        if r["pmid"] in counted:
            studies_of[r["pmid"]].add(r["nct_id"])
    rows = []
    for year in request.period.years:
        here = [c for c in counted.values() if c.year == year]
        referenced = [c for c in here if studies_of.get(c.pmid)]
        studies = {n for c in here for n in studies_of.get(c.pmid, ())}
        rows.append({"year": year, "papers": len(here), "referenced": len(referenced),
                     "studies": len(studies),
                     "share": share(len(referenced), len(here))})  # fmt: skip
    records = paper_records(counted)
    for r in records:
        r["studies"] = sorted(studies_of.get(r["pmid"], ()))
    total_ref = sum(1 for c in counted if studies_of.get(c))
    all_studies = {n for c in counted for n in studies_of.get(c, ())}
    notes = basis_notes(counted, basis)
    if counted:
        studies_word = "study" if len(all_studies) == 1 else "studies"
        notes.append(f"{total_ref:,} of {len(counted):,} papers are listed by "
                     f"{len(all_studies):,} registered {studies_word}.")  # fmt: skip
    notes += _recent_note(request)
    return Figure(
        request,
        [Column("year", "Year", "year"),
         Column("papers", "Papers", "count", "papers"),
         Column("referenced", "Listed by registered studies", "count", "papers"),
         Column("share", "Share listed", "share"),
         Column("studies", "Studies listing them", "count", "studies")],
        rows,
        totals={"papers": len(counted), "referenced": total_ref, "studies": len(all_studies)},
        notes=notes,
        record_columns=[*PAPER_RECORD_COLUMNS, Column("studies", "Studies (NCT IDs)", "list")],
        records=records,
    )  # fmt: skip


IMPACT_STUDIES = register(
    Metric(
        id="impact.studies",
        version=1,
        name="Papers in registered studies",
        area="Impact",
        definition="How many of the cohort's papers in the period are listed as references by "
        "studies registered with ClinicalTrials.gov.",
        counting=f"{PAPER_RULE} {WINDOW_RULE} A paper is listed when a registry record names it "
        "among its references (background or results) and the link is accepted. Each study "
        "counts once per year however many of the year's papers it lists.",
        unit="papers",
        sources=("pubmed", "ctgov"),
        options=(BASIS,),
        caveats=(PUBMED_CAVEAT, REGISTRY_CAVEAT, ASSOCIATION_CAVEAT),
        needs=("paper_links", "papers", "study_references"),
        compute=_studies,
    )
)

__all__ = ["IMPACT_CLINICAL", "IMPACT_STUDIES"]
