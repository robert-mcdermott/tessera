"""Topic figures (INT-06): what a cohort publishes on, and how that changes.

A paper counts toward a topic when the topic is one of its major MeSH headings (the ones
PubMed marks with an asterisk). Headings can be rolled up to research areas, the tops of the
MeSH branches that name what people study (diseases, anatomy, organisms, processes); a paper
counts once per area. Trends are claimed only when there are enough papers and years.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from tessera.insight.figure import Column, Figure, FigureRequest, share
from tessera.insight.inputs import Inputs
from tessera.insight.metrics import BASIS, PAPER_RULE, WINDOW_RULE, Metric, Option, register
from tessera.insight.publications import (
    PAPER_RECORD_COLUMNS,
    PUBMED_CAVEAT,
    basis_notes,
    counted_papers,
    paper_records,
    partial_year_note,
)

LEVEL = Option(
    "level", "Topics as", (("heading", "MeSH headings"), ("area", "Research areas")), "heading"
)
TOP = 30  # topics listed in the landscape
TRENDED = 12  # topics followed year by year

TOPIC_RULE = (
    "A paper counts toward a topic when the topic is one of its major MeSH headings (the "
    "headings PubMed marks as the paper's main subjects). As research areas, headings are "
    "rolled up to the tops of the MeSH branches that name what people study (diseases, "
    "anatomy, organisms, psychology, and processes), and a paper counts once per area. A "
    "topic's share is the part of the cohort's papers that are on it."
)
TREND_RULE = (
    "Trends compare a topic's share of the cohort's papers in the later and the earlier half "
    "of the period's complete years (at most three years each; the year under way is left "
    "out). A topic is rising when its share grew by a quarter or more and falling when it "
    "shrank by a fifth or more, and steady otherwise, but only when it has at least the "
    "small-number threshold of papers in each half; otherwise there are too few papers to "
    "tell. With fewer than four complete years there are too few years to tell."
)
MESH_CAVEAT = (
    "MeSH headings are assigned by NLM indexers, usually some weeks after publication, so the "
    "newest papers may have no headings yet."
)


def _topics(inputs: Inputs, level: str) -> tuple[dict[str, set[str]], dict[str, str], bool]:
    """Each paper's topics, the topics' names, and whether areas were available."""
    by_paper: dict[str, set[str]] = defaultdict(set)
    names: dict[str, str] = {}
    areas: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for r in inputs.rows("mesh_areas"):
        areas[r["descriptor_ui"]].append((r["area_tree"], r["area"]))
    use_areas = level == "area" and bool(areas)
    for r in inputs.rows("paper_topics"):
        if use_areas:
            for tree, name in areas.get(r["descriptor_ui"], ()):
                by_paper[r["pmid"]].add(tree)
                names[tree] = name
        else:
            by_paper[r["pmid"]].add(r["descriptor_ui"])
            names[r["descriptor_ui"]] = r["descriptor"] or r["descriptor_ui"]
    return by_paper, names, use_areas


def _halves(request: FigureRequest) -> tuple[list[int], list[int]] | None:
    complete = [y for y in request.period.years if y < request.today.year]
    if len(complete) < 4:
        return None
    half = min(3, len(complete) // 2)
    return complete[-2 * half : -half], complete[-half:]


def trend_of(
    counts: dict[int, int], totals: dict[int, int], halves: tuple[list[int], list[int]] | None,
    small: int,
) -> tuple[str, float | None]:  # fmt: skip
    """("rising" | "falling" | "steady" | "too few papers" | "too few years", change)."""
    if halves is None:
        return "too few years", None
    earlier, later = halves
    before, after = sum(counts.get(y, 0) for y in earlier), sum(counts.get(y, 0) for y in later)
    if before < small or after < small:
        return "too few papers", None
    share_before = before / max(sum(totals.get(y, 0) for y in earlier), 1)
    share_after = after / max(sum(totals.get(y, 0) for y in later), 1)
    change = round(share_after / share_before - 1, 3)
    if change >= 0.25:
        return "rising", change
    if change <= -0.2:
        return "falling", change
    return "steady", change


TREND_LABELS = {
    "rising": "Rising",
    "falling": "Falling",
    "steady": "Steady",
    "too few papers": "Too few papers to tell",
    "too few years": "Too few years to tell",
}


def _landscape(inputs: Inputs, request: FigureRequest, *, by_year: bool) -> Figure:
    basis, level = request.options["basis"], request.options["level"]
    counted = counted_papers(inputs, request.period, basis)
    by_paper, names, use_areas = _topics(inputs, level)
    per_topic: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    totals: dict[int, int] = defaultdict(int)
    indexed = 0
    for pmid, paper in counted.items():
        totals[paper.year] += 1
        topics = by_paper.get(pmid, set())
        indexed += bool(topics)
        for topic in topics:
            per_topic[topic][paper.year] += 1
    halves = _halves(request)
    ranked = sorted(per_topic, key=lambda t: (-sum(per_topic[t].values()), names[t]))
    rows = []
    for topic in ranked[: TRENDED if by_year else TOP]:
        papers = sum(per_topic[topic].values())
        trend, change = trend_of(per_topic[topic], totals, halves, request.small_number)
        row: dict[str, Any] = {
            "key": topic,
            "label": names[topic],
            "papers": papers,
            "share": share(papers, len(counted)),
            "trend": TREND_LABELS[trend],
            "change": change,
        }
        if by_year:
            for year in request.period.years:
                row[f"y{year}"] = per_topic[topic].get(year, 0)
        rows.append(row)
    word = "Research area" if use_areas else "Topic"
    columns = [
        Column("label", word, "label"),
        Column("papers", "Papers", "count", "papers"),
        Column("share", "Share of papers", "share"),
        Column("trend", "Trend", "label"),
        Column("change", "Change in share", "share"),
    ]
    if by_year:
        columns[1:1] = [Column(f"y{y}", str(y), "count", "papers") for y in request.period.years]
    notes = basis_notes(counted, basis) + partial_year_note(request)
    if level == "area" and not use_areas:
        notes.append("MeSH's hierarchy is not loaded, so topics are shown as headings.")
    if counted and indexed < len(counted):
        why = "no major heading in a research area" if use_areas else "no major MeSH heading yet"
        notes.append(
            f"{len(counted) - indexed:,} of {len(counted):,} papers have {why} and count toward "
            "no topic."
        )
    if halves:
        earlier, later = halves
        notes.append(
            f"Trends compare {_span(later)} with {_span(earlier)}; the year under way is left out."
        )
    records = paper_records(counted)
    for r in records:
        r["topics"] = sorted(names[t] for t in by_paper.get(r["pmid"], ()))
    return Figure(
        request, columns, rows,
        totals={"papers": len(counted), "topics": len(per_topic), "indexed": indexed},
        notes=notes, record_columns=[*PAPER_RECORD_COLUMNS, Column("topics", "Topics", "list")],
        records=records,
    )  # fmt: skip


def _span(years: list[int]) -> str:
    return str(years[0]) if len(years) == 1 else f"{years[0]}\u2013{years[-1]}"  # an en dash


COMMON: dict[str, Any] = {
    "area": "Topics",
    "unit": "papers",
    "sources": ("pubmed", "mesh"),
    "options": (BASIS, LEVEL),
    "caveats": (PUBMED_CAVEAT, MESH_CAVEAT),
    "needs": ("paper_links", "papers", "paper_topics", "mesh_areas"),
}

register(Metric(
    id="topics.landscape", version=1, name="Topics",
    definition="What the cohort's papers in the period are about: their major MeSH headings or "
    "research areas, with how many papers are on each, its share, and its trend.",
    counting=f"{TOPIC_RULE} {TREND_RULE} {PAPER_RULE} {WINDOW_RULE}",
    compute=lambda inputs, request: _landscape(inputs, request, by_year=False), **COMMON,
))  # fmt: skip
register(Metric(
    id="topics.by_year", version=1, name="Topics by year",
    definition=f"The cohort's {TRENDED} largest topics in the period, with their papers in each "
    "year and their trend.",
    counting=f"{TOPIC_RULE} {TREND_RULE} {PAPER_RULE} {WINDOW_RULE}",
    compute=lambda inputs, request: _landscape(inputs, request, by_year=True), **COMMON,
))  # fmt: skip

__all__ = ["TREND_LABELS", "trend_of"]
