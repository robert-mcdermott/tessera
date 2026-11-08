"""Publication figures, and the counting rule that topic figures share."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tessera.insight.cohorts import members_from_table
from tessera.insight.figure import Column, Figure, FigureRequest, Period, share
from tessera.insight.inputs import Inputs
from tessera.insight.metrics import BASIS, PAPER_RULE, WINDOW_RULE, Metric, register

PUBMED_CAVEAT = (
    "PubMed covers biomedical journals; papers in venues it does not index (many conference "
    "papers, preprints, and books) are missing."
)


@dataclass
class Counted:
    pmid: str
    year: int
    title: str | None
    journal: str | None
    doi: str | None
    confirmed: bool = False
    people: set[str] = field(default_factory=set)
    links: list[str] = field(default_factory=list)

    def record(self) -> dict[str, Any]:
        return {
            "pmid": self.pmid,
            "year": self.year,
            "title": self.title,
            "journal": self.journal,
            "doi": self.doi,
            "status": "confirmed" if self.confirmed else "likely",
            "members": sorted(self.people),
            "links": sorted(self.links),
        }


PAPER_RECORD_COLUMNS = [
    Column("pmid", "PubMed ID", "ref"),
    Column("year", "Year", "year"),
    Column("title", "Title", "text"),
    Column("journal", "Journal", "text"),
    Column("doi", "DOI", "ref"),
    Column("status", "Counted as", "label"),
    Column("members", "Members", "list"),
    Column("links", "Links", "list"),
]


def counted_papers(inputs: Inputs, period: Period, basis: str) -> dict[str, Counted]:
    """The papers that count for the cohort in the period, each once."""
    members = members_from_table(inputs.tables["members"])
    papers = {r["pmid"]: r for r in inputs.rows("papers")}
    out: dict[str, Counted] = {}
    for link in inputs.rows("paper_links"):
        if basis == "confirmed" and not link["confirmed"]:
            continue
        paper = papers.get(link["pmid"])
        if paper is None or paper["retracted"] or paper["year"] not in period:
            continue
        member = members.get(link["person_id"])
        if member is None or not member.counts(paper["year"]):
            continue
        counted = out.get(paper["pmid"])
        if counted is None:
            counted = out[paper["pmid"]] = Counted(
                paper["pmid"], paper["year"], paper["title"], paper["journal"], paper["doi"]
            )
        counted.confirmed = counted.confirmed or bool(link["confirmed"])
        counted.people.add(member.name)
        counted.links.append(link["assertion_id"])
    return out


def paper_records(counted: dict[str, Counted]) -> list[dict[str, Any]]:
    return [c.record() for c in sorted(counted.values(), key=lambda c: (-c.year, c.pmid))]


def basis_notes(counted: dict[str, Counted], basis: str) -> list[str]:
    likely = sum(1 for c in counted.values() if not c.confirmed)
    if basis == "confirmed":
        return ["Only confirmed papers are counted."]
    if likely:
        return [
            f"{likely:,} of {len(counted):,} papers are strong suggestions that nobody has "
            "confirmed yet; choose confirmed only to leave them out."
        ]
    return []


def partial_year_note(request: FigureRequest) -> list[str]:
    if request.period.end >= request.today.year:
        return [f"{request.today.year} is not over; its numbers will grow."]
    return []


def _by_year(inputs: Inputs, request: FigureRequest) -> Figure:
    basis = request.options["basis"]
    counted = counted_papers(inputs, request.period, basis)
    rows = []
    for year in request.period.years:
        here = [c for c in counted.values() if c.year == year]
        confirmed = sum(1 for c in here if c.confirmed)
        row: dict[str, Any] = {"year": year, "papers": len(here), "confirmed": confirmed}
        if basis == "likely":
            row["likely"] = len(here) - confirmed
        rows.append(row)
    columns = [
        Column("year", "Year", "year"),
        Column("papers", "Papers", "count", "papers"),
        Column("confirmed", "Confirmed", "count", "papers"),
    ]
    if basis == "likely":
        columns.append(Column("likely", "Likely", "count", "papers"))
    confirmed_total = sum(r["confirmed"] for r in rows)
    totals: dict[str, Any] = {"papers": len(counted), "confirmed": confirmed_total}
    if basis == "likely":
        totals["likely"] = len(counted) - confirmed_total
    return Figure(
        request,
        columns,
        rows,
        totals=totals,
        notes=basis_notes(counted, basis) + partial_year_note(request),
        record_columns=PAPER_RECORD_COLUMNS,
        records=paper_records(counted),
    )


PUBLICATIONS_BY_YEAR = register(
    Metric(
        id="publications.count",
        version=1,
        name="Publications by year",
        area="Publications",
        definition="The cohort's papers published in each year of the period.",
        counting=f"{PAPER_RULE} {WINDOW_RULE}",
        unit="papers",
        sources=("pubmed",),
        options=(BASIS,),
        caveats=(PUBMED_CAVEAT,),
        needs=("paper_links", "papers"),
        compute=_by_year,
    )
)

__all__ = [
    "PAPER_RECORD_COLUMNS",
    "PUBLICATIONS_BY_YEAR",
    "Counted",
    "basis_notes",
    "counted_papers",
    "paper_records",
    "partial_year_note",
]


# --- field-normalized citation ---------------------------------------------------------------

BANDS = (
    ("below_half", "Below 0.5", 0.0, 0.5),
    ("half_to_one", "0.5 to 1", 0.5, 1.0),
    ("one_to_two", "1 to 2", 1.0, 2.0),
    ("two_to_five", "2 to 5", 2.0, 5.0),
    ("five_up", "5 or more", 5.0, float("inf")),
)


def _influence(inputs: Inputs, request: FigureRequest) -> Figure:
    basis = request.options["basis"]
    counted = counted_papers(inputs, request.period, basis)
    rcr = {r["pmid"]: r["rcr"] for r in inputs.rows("citations")}
    recent = request.today.year - 1  # iCite's ratios are provisional for two years
    counts: dict[str, int] = dict.fromkeys([b[0] for b in BANDS] + ["recent", "none"], 0)
    records = paper_records(counted)
    for r in records:
        value = rcr.get(r["pmid"])
        r["rcr"] = value
        if r["year"] >= recent:
            band = "recent"
        elif value is None:
            band = "none"
        else:
            band = next(key for key, _, low, high in BANDS if low <= value < high)
        r["band"] = band
        counts[band] += 1
    banded = sum(counts[b[0]] for b in BANDS)
    rows = [
        {"key": key, "label": label, "papers": counts[key], "share": share(counts[key], banded)}
        for key, label, _, _ in BANDS
    ]
    rows.append({"key": "recent", "label": f"Published {recent} or later (provisional)",
                 "papers": counts["recent"], "share": None})  # fmt: skip
    rows.append({"key": "none", "label": "No ratio in iCite", "papers": counts["none"],
                 "share": None})  # fmt: skip
    notes = basis_notes(counted, basis)
    notes.append(
        "Shares are of the papers with a settled ratio. A ratio of 1 means a paper is cited as "
        "often as NIH-funded papers in its field and year; papers from the last two years are "
        "kept apart because their ratios are still provisional."
    )
    above = sum(counts[k] for k in ("one_to_two", "two_to_five", "five_up"))
    if banded:
        notes.append(f"{above:,} of {banded:,} papers ({above / banded:.0%}) are cited at or above "
                     "the field's rate.")  # fmt: skip
    return Figure(
        request,
        [Column("label", "Relative citation ratio", "label"),
         Column("papers", "Papers", "count", "papers"), Column("share", "Share", "share")],
        rows,
        totals={"papers": len(counted), "banded": banded, "at_or_above": above},
        notes=notes,
        record_columns=[*PAPER_RECORD_COLUMNS, Column("rcr", "Relative citation ratio", "text"),
                        Column("band", "Band", "label")],
        records=records,
    )  # fmt: skip


PUBLICATIONS_INFLUENCE = register(
    Metric(
        id="publications.influence",
        version=1,
        name="Field-normalized citation",
        area="Publications",
        definition="How often the cohort's papers in the period are cited, against papers in "
        "their own field and year: the distribution of iCite's relative citation ratio.",
        counting=f"{PAPER_RULE} {WINDOW_RULE} Each paper falls in one band of its relative "
        "citation ratio (RCR) from NIH's iCite; papers published in the last two calendar years "
        "are counted apart, because iCite's ratio is provisional until then.",
        unit="papers",
        sources=("pubmed", "icite"),
        options=(BASIS,),
        caveats=(
            PUBMED_CAVEAT,
            "A citation ratio describes papers, not people: it says nothing about the quality "
            "of anyone's work, and Insight never ranks people by it.",
        ),
        needs=("paper_links", "papers", "citations"),
        compute=_influence,
    )
)
