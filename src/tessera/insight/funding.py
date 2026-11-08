"""Funding figures (INT-02, INT-05): money counted the way a sponsored-research office counts it.

* Money comes from RePORTER's fiscal-year records. Each record is one award action (a new
  award, a renewal, a continuing year, or a supplement) and counts once, in its fiscal year.
* Records of subprojects (components of multi-project awards) are left out: the parent
  award's record already includes their money.
* A grant (one core project number) counts once for a cohort, however many members are on it.
* The organization's portfolio is every award to the organization. A cohort's is the awards to
  the organization on which a member is an investigator (or awards anywhere, when asked),
  counted in the fiscal years the member was a member.
* Every figure names the amount it shows: total cost, direct costs, or indirect costs.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from tessera.insight.cohorts import members_from_table
from tessera.insight.figure import Column, Figure, FigureRequest, share
from tessera.insight.inputs import Inputs
from tessera.insight.metrics import WINDOW_RULE, Metric, Option, register

AMOUNT = Option(
    "amount",
    "Amount",
    (("total", "Total cost"), ("direct", "Direct costs"), ("indirect", "Indirect costs")),
    "total",
)
AWARDEE = Option(
    "awardee",
    "Awarded to",
    (("organization", "The organization"), ("anywhere", "Anywhere")),
    "organization",
)
MONTHS = Option(
    "months",
    "Ending within",
    (("6", "6 months"), ("12", "12 months"), ("24", "24 months")),
    "12",
)
KIND = Option("kind", "Groups of kind", (), "program", free=True)

FUNDING_RULE = (
    "Money comes from RePORTER's fiscal-year records: each record is one award action (a new "
    "award, a renewal, a continuing year, or a supplement) and counts once, in its fiscal year. "
    "Records of subprojects of multi-project awards are left out, because the parent award's "
    "record already includes their money. A grant (one core project number) counts once however "
    "many members are on it. The organization's portfolio is every award to the organization; "
    "another cohort's is the awards to the organization on which a member is an investigator "
    "(or awards anywhere, when chosen), counted in the fiscal years the member was a member."
)
FUNDING_CAVEATS = (
    "Amounts are what was awarded, not what was spent, and include only funders that report to "
    "NIH RePORTER (NIH and some other HHS agencies). Foundation, industry, and most other "
    "federal funding is not there.",
    "Public data has awards only, never applications, so success rates cannot be computed.",
    "A member who leads one project of a multi-project award is linked to the whole award.",
)

APPLICATION_TYPES = {"1": "new", "2": "renewal", "3": "supplement"}  # anything else: continuing
TYPE_LABELS = {
    "new": "New awards",
    "renewal": "Renewals",
    "continuing": "Continuing years",
    "supplement": "Supplements",
}
TYPE_ORDER = ("new", "renewal", "continuing", "supplement")

# NIH institutes and centers, and other agencies that report to RePORTER, by abbreviation.
INSTITUTES = {
    "NCI": "National Cancer Institute",
    "NEI": "National Eye Institute",
    "NHGRI": "National Human Genome Research Institute",
    "NHLBI": "National Heart, Lung, and Blood Institute",
    "NIA": "National Institute on Aging",
    "NIAAA": "National Institute on Alcohol Abuse and Alcoholism",
    "NIAID": "National Institute of Allergy and Infectious Diseases",
    "NIAMS": "National Institute of Arthritis and Musculoskeletal and Skin Diseases",
    "NIBIB": "National Institute of Biomedical Imaging and Bioengineering",
    "NICHD": "National Institute of Child Health and Human Development",
    "NIDA": "National Institute on Drug Abuse",
    "NIDCD": "National Institute on Deafness and Other Communication Disorders",
    "NIDCR": "National Institute of Dental and Craniofacial Research",
    "NIDDK": "National Institute of Diabetes and Digestive and Kidney Diseases",
    "NIEHS": "National Institute of Environmental Health Sciences",
    "NIGMS": "National Institute of General Medical Sciences",
    "NIMH": "National Institute of Mental Health",
    "NIMHD": "National Institute on Minority Health and Health Disparities",
    "NINDS": "National Institute of Neurological Disorders and Stroke",
    "NINR": "National Institute of Nursing Research",
    "NLM": "National Library of Medicine",
    "NCATS": "National Center for Advancing Translational Sciences",
    "NCCIH": "National Center for Complementary and Integrative Health",
    "FIC": "Fogarty International Center",
    "OD": "NIH Office of the Director",
    "AHRQ": "Agency for Healthcare Research and Quality",
    "CDC": "Centers for Disease Control and Prevention",
    "FDA": "Food and Drug Administration",
    "HRSA": "Health Resources and Services Administration",
    "SAMHSA": "Substance Abuse and Mental Health Services Administration",
    "VA": "Department of Veterans Affairs",
}


# RePORTER's funding mechanisms, in words.
MECHANISMS = {
    "Non-SBIR/STTR": "Research projects (not SBIR/STTR)",
    "SBIR/STTR": "Small business research (SBIR/STTR)",
    "Research Centers": "Research centers",
    "Other Research-Related": "Other research-related, including career awards",
    "Training, Individual": "Training, individual",
    "Training, Institutional": "Training, institutional",
    "Construction": "Construction",
    "R&D Contracts": "Research and development contracts",
    "Intramural": "Intramural research",
    "Interagency Agreements": "Interagency agreements",
    "Other": "Other",
}


def mechanism_label(value: str | None) -> str:
    if not value:
        return "Not stated"
    return MECHANISMS.get(value, value)


def institute_label(code: str | None) -> str:
    if not code:
        return "Not stated"
    return f"{code} · {INSTITUTES[code]}" if code in INSTITUTES else code


def application_type(project_num: str | None) -> str:
    first = (project_num or " ")[0]
    return APPLICATION_TYPES.get(first, "continuing")


@dataclass
class Counted:
    """One fiscal-year record that counts, and who it counts for."""

    record: dict[str, Any]
    members: set[str] = field(default_factory=set)
    links: list[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return application_type(self.record["project_num"])

    def amount(self, which: str) -> int | None:
        return self.record[which]


def counted_awards(
    inputs: Inputs, request: FigureRequest, *, any_year: bool = False
) -> list[Counted]:
    """The fiscal-year records that count for the cohort in the period (or in any year)."""
    members = members_from_table(inputs.tables["members"])
    links: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in inputs.rows("grant_links"):
        links[link["core"]].append(link)
    organization = request.cohort.get("kind") == "organization"
    anywhere = request.options.get("awardee") == "anywhere"
    out = []
    for record in inputs.rows("awards"):
        if record["subproject_id"]:
            continue
        year = record["fiscal_year"]
        if not any_year and year not in request.period:
            continue
        counted = Counted(record)
        for link in links.get(record["core"], ()):
            member = members.get(link["person_id"])
            if member is not None and member.counts(year):
                counted.members.add(member.name)
                counted.links.append(link["assertion_id"])
        if organization:
            if record["ours"] or (anywhere and counted.members):
                out.append(counted)
        elif counted.members and (record["ours"] or anywhere):
            out.append(counted)
    return out


AWARD_RECORD_COLUMNS = [
    Column("appl_id", "Application", "ref"),
    Column("core_project_num", "Grant", "ref"),
    Column("project_num", "Project number", "text"),
    Column("fiscal_year", "Fiscal year", "year"),
    Column("type", "Award type", "label"),
    Column("title", "Title", "text"),
    Column("activity", "Activity", "label"),
    Column("institute", "Institute", "label"),
    Column("mechanism", "Mechanism", "label"),
    Column("amount", "Amount", "money", "dollars"),
    Column("total", "Total cost", "money", "dollars"),
    Column("direct", "Direct costs", "money", "dollars"),
    Column("indirect", "Indirect costs", "money", "dollars"),
    Column("organization", "Awarded to", "text"),
    Column("members", "Members", "list"),
    Column("links", "Links", "list"),
]


def award_records(counted: list[Counted], which: str) -> list[dict[str, Any]]:
    rows = []
    for c in sorted(counted, key=lambda c: (-c.record["fiscal_year"], c.record["core"],
                                            c.record["appl_id"])):  # fmt: skip
        r = c.record
        rows.append(
            {
                "appl_id": r["appl_id"], "core_project_num": r["core"],
                "project_num": r["project_num"], "fiscal_year": r["fiscal_year"],
                "type": TYPE_LABELS[c.kind], "title": r["title"], "activity": r["activity"],
                "institute": r["institute"], "mechanism": r["mechanism"], "amount": r[which],
                "total": r["total"], "direct": r["direct"], "indirect": r["indirect"],
                "organization": r["organization"], "members": sorted(c.members),
                "links": sorted(c.links),
            }
        )  # fmt: skip
    return rows


def amount_notes(counted: list[Counted], which: str, request: FigureRequest) -> list[str]:
    label = AMOUNT.label_of(which).lower()
    notes = [f"Amounts are {label}, in US dollars as awarded."]
    missing = sum(1 for c in counted if c.amount(which) is None)
    if missing:
        notes.append(
            f"{missing:,} of {len(counted):,} records do not state their {label}; they add "
            "nothing to the amounts but still count as grants."
        )
    if request.period.end >= _fiscal_year(request.today):
        notes.append(
            f"FY{_fiscal_year(request.today)} is under way; awards are added through September."
        )
    return notes


def _fiscal_year(day: date) -> int:
    return day.year + 1 if day.month >= 10 else day.year


def _sum(counted: list[Counted], which: str) -> int:
    return sum(c.amount(which) or 0 for c in counted)


def _by_year(inputs: Inputs, request: FigureRequest) -> Figure:
    which = request.options["amount"]
    counted = counted_awards(inputs, request)
    rows = []
    for year in request.period.years:
        here = [c for c in counted if c.record["fiscal_year"] == year]
        row: dict[str, Any] = {"fiscal_year": year, "amount": _sum(here, which)}
        for kind in TYPE_ORDER:
            row[kind] = _sum([c for c in here if c.kind == kind], which)
        row["grants"] = len({c.record["core"] for c in here})
        row["records"] = len(here)
        rows.append(row)
    columns = [
        Column("fiscal_year", "Fiscal year", "year"),
        Column("amount", AMOUNT.label_of(which), "money", "dollars"),
        *(Column(k, TYPE_LABELS[k], "money", "dollars") for k in TYPE_ORDER),
        Column("grants", "Grants", "count", "grants"),
        Column("records", "Records", "count", "records"),
    ]
    totals = {
        "amount": _sum(counted, which),
        "grants": len({c.record["core"] for c in counted}),
        "records": len(counted),
        "active": len({c.record["core"] for c in counted
                       if c.record["project_end"] and c.record["project_end"] >= request.today}),
    }  # fmt: skip
    return Figure(
        request, columns, rows, totals=totals, notes=amount_notes(counted, which, request),
        record_columns=AWARD_RECORD_COLUMNS, records=award_records(counted, which),
    )  # fmt: skip


def _by_category(key: str, label: str, name_of: Any = None) -> Any:
    def compute(inputs: Inputs, request: FigureRequest) -> Figure:
        which = request.options["amount"]
        counted = counted_awards(inputs, request)
        total = _sum(counted, which)
        groups: dict[str | None, list[Counted]] = defaultdict(list)
        for c in counted:
            groups[c.record[key]].append(c)
        rows = []
        for value, here in groups.items():
            amount = _sum(here, which)
            row = {
                "key": value or "",
                "label": (value or "Not stated")
                if key == "institute"
                else (name_of(value) if name_of else (value or "Not stated")),
                "amount": amount,
                "share": share(amount, total),
                "grants": len({c.record["core"] for c in here}),
            }
            if key == "institute":
                row["detail"] = INSTITUTES.get(value or "", "")
            if key == "activity":
                mechanisms = sorted(
                    {mechanism_label(c.record["mechanism"]) for c in here if c.record["mechanism"]}
                )
                row["mechanism"] = "; ".join(mechanisms)
            rows.append(row)
        rows.sort(key=lambda r: (-r["amount"], -r["grants"], r["label"]))
        columns = [
            Column("label", label, "label"),
            Column("amount", AMOUNT.label_of(which), "money", "dollars"),
            Column("share", "Share", "share"),
            Column("grants", "Grants", "count", "grants"),
        ]
        if key == "activity":
            columns.append(Column("mechanism", "Mechanism", "text"))
        if key == "institute":
            columns.insert(1, Column("detail", "Name", "text"))
        notes = amount_notes(counted, which, request)
        notes.append("Shares are of the amount; a grant moved between categories over the years "
                     "counts in each.")  # fmt: skip
        return Figure(
            request, columns, rows,
            totals={"amount": total, "grants": len({c.record["core"] for c in counted})},
            notes=notes, record_columns=AWARD_RECORD_COLUMNS,
            records=award_records(counted, which),
        )  # fmt: skip

    return compute


def _ending(inputs: Inputs, request: FigureRequest) -> Figure:
    """Grants whose projects end soon. Labeled as end dates: an ending project is not a gap in
    funding (it may be renewed, extended, or replaced)."""
    months = int(request.options["months"])
    horizon = request.today + timedelta(days=round(months * 30.44))
    counted = counted_awards(inputs, request, any_year=True)
    latest: dict[str, Counted] = {}
    members: dict[str, set[str]] = defaultdict(set)
    for c in counted:
        core = c.record["core"]
        members[core] |= c.members
        if core not in latest or c.record["fiscal_year"] >= latest[core].record["fiscal_year"]:
            latest[core] = c
    rows = []
    for core, c in latest.items():
        end = c.record["project_end"]
        if end is None or not request.today <= end <= horizon:
            continue
        rows.append(
            {
                "core_project_num": core, "title": c.record["title"],
                "activity": c.record["activity"], "institute": c.record["institute"],
                "project_end": end.isoformat(), "latest_fiscal_year": c.record["fiscal_year"],
                "latest_amount": c.record["total"], "members": sorted(members[core]),
            }
        )  # fmt: skip
    rows.sort(key=lambda r: (r["project_end"], r["core_project_num"]))
    columns = [
        Column("core_project_num", "Grant", "ref"),
        Column("title", "Title", "text"),
        Column("activity", "Activity", "label"),
        Column("institute", "Institute", "label"),
        Column("project_end", "Project end date", "date"),
        Column("latest_fiscal_year", "Latest award", "year"),
        Column("latest_amount", "Latest total cost", "money", "dollars"),
        Column("members", "Members", "list"),
    ]
    figure = Figure(
        request, columns, rows,
        totals={"grants": len(rows), "latest_amount": sum(r["latest_amount"] or 0 for r in rows)},
        notes=[
            f"Project end dates from {request.today.isoformat()} to {horizon.isoformat()}, from "
            "each grant's latest record. An end date is not a gap in funding: projects are often "
            "renewed, extended without new money, or followed by new awards.",
        ],
        record_columns=columns, records=rows,
    )  # fmt: skip
    figure.period_view = {
        "start": request.today.year, "end": horizon.year, "basis": "upcoming",
        "label": f"The next {months} months",
    }  # fmt: skip
    return figure


def _by_group(inputs: Inputs, request: FigureRequest) -> Figure:
    which, kind = request.options["amount"], request.options["kind"]
    counted = counted_awards(inputs, request)
    names: dict[str, tuple[str, str]] = {}
    spans: dict[tuple[str, str], list[tuple[int | None, int | None]]] = defaultdict(list)
    for m in inputs.rows("group_members"):
        if m["kind"] != kind:
            continue
        names[m["group_id"]] = (m["name"], m["slug"])
        spans[(m["group_id"], m["person_id"])].append((m["start_year"], m["end_year"]))
    links_by_core: dict[str, set[str]] = defaultdict(set)
    for link in inputs.rows("grant_links"):
        links_by_core[link["core"]].add(link["person_id"])

    def in_group(gid: str, pid: str, year: int) -> bool:
        return any((s is None or year >= s) and (e is None or year <= e)
                   for s, e in spans.get((gid, pid), ()))  # fmt: skip

    rows = []
    for gid, (name, slug) in names.items():
        here = [
            c for c in counted
            if any(in_group(gid, pid, c.record["fiscal_year"])
                   for pid in links_by_core.get(c.record["core"], ()))
        ]  # fmt: skip
        rows.append(
            {"group": name, "slug": slug, "amount": _sum(here, which),
             "grants": len({c.record["core"] for c in here}),
             "members": len({pid for (g, pid) in spans if g == gid})}
        )  # fmt: skip
    rows.sort(key=lambda r: r["group"].lower())  # by name: no league tables
    columns = [
        Column("group", "Group", "label"),
        Column("amount", AMOUNT.label_of(which), "money", "dollars"),
        Column("grants", "Grants", "count", "grants"),
        Column("members", "Members in the cohort", "count", "people"),
    ]
    notes = amount_notes(counted, which, request)
    notes.append(
        "A grant counts for every group with a member on it, so the groups' amounts overlap and "
        "do not add up to the cohort's total. Groups are listed by name."
    )
    if not names:
        notes.append(f"No group of kind {kind!r} has members in this cohort.")
    return Figure(
        request, columns, rows, totals={"groups": len(rows)}, notes=notes,
        record_columns=AWARD_RECORD_COLUMNS, records=award_records(counted, which),
    )  # fmt: skip


COMMON = {
    "area": "Funding",
    "unit": "dollars",
    "sources": ("reporter",),
    "period": "fiscal",
    "caveats": FUNDING_CAVEATS,
    "needs": ("grant_links", "awards"),
}

register(Metric(
    id="funding.awarded", version=1, name="Awards by fiscal year",
    definition="Money awarded to the cohort in each fiscal year of the period, by award type: "
    "new awards, renewals, continuing years, and supplements.",
    counting=f"{FUNDING_RULE} {WINDOW_RULE}", options=(AMOUNT, AWARDEE), compute=_by_year,
    **COMMON,
))  # fmt: skip
register(Metric(
    id="funding.by_institute", version=1, name="Awards by funding institute",
    definition="Money awarded to the cohort over the period, by the institute or agency that "
    "administers each award, with each one's share.",
    counting=f"{FUNDING_RULE} {WINDOW_RULE}", options=(AMOUNT, AWARDEE),
    compute=_by_category("institute", "Institute"), **COMMON,
))  # fmt: skip
register(Metric(
    id="funding.by_activity", version=1, name="Awards by activity code",
    definition="Money awarded to the cohort over the period, by activity code (R01, P30, K99, "
    "T32, and so on), with each code's funding mechanism.",
    counting=f"{FUNDING_RULE} {WINDOW_RULE}", options=(AMOUNT, AWARDEE),
    compute=_by_category("activity", "Activity code"), **COMMON,
))  # fmt: skip
register(Metric(
    id="funding.by_mechanism", version=1, name="Awards by funding mechanism",
    definition="Money awarded to the cohort over the period, by funding mechanism: research "
    "grants, research centers, training, and the rest.",
    counting=f"{FUNDING_RULE} {WINDOW_RULE}", options=(AMOUNT, AWARDEE),
    compute=_by_category("mechanism", "Mechanism", mechanism_label), **COMMON,
))  # fmt: skip
register(Metric(
    id="funding.by_group", version=1, name="Awards by group",
    definition="Money awarded to the cohort over the period, for each group of one kind "
    "(programs, divisions, labs, and so on) with members in the cohort.",
    counting=f"{FUNDING_RULE} A grant counts for a group when one of its investigators was a "
    "member of the group in that fiscal year (where membership dates are known).",
    options=(AMOUNT, AWARDEE, KIND), compute=_by_group,
    **{**COMMON, "needs": ("grant_links", "awards", "group_members")},
))  # fmt: skip
register(Metric(
    id="funding.ending", version=1, name="Grants by project end date",
    definition="The cohort's grants whose projects end within the next months, from each "
    "grant's latest record. These are end dates, not gaps in funding.",
    counting="A grant is listed when the project end date on its latest fiscal-year record "
    "falls between today and the end of the chosen window. Grants count as for the other "
    "funding figures, in any fiscal year.",
    options=(AWARDEE, MONTHS), compute=_ending,
    **{**COMMON, "unit": "grants", "period": "upcoming"},
))  # fmt: skip

__all__ = [
    "INSTITUTES",
    "MECHANISMS",
    "application_type",
    "counted_awards",
    "institute_label",
    "mechanism_label",
]
