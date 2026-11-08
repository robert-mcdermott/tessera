"""Study figures (INT-07): a cohort's clinical studies by registry status, phase, sponsor class,
intervention, condition, and start year.

Status is always the registry's overall status as the sponsor reported it to
ClinicalTrials.gov. It is never shown as local recruitment (CLN-02), and no patient-level data
is stored or shown (CLN-03).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from tessera.insight.cohorts import members_from_table
from tessera.insight.figure import Column, Figure, FigureRequest, share
from tessera.insight.inputs import Inputs
from tessera.insight.metrics import BASIS, WINDOW_RULE, Metric, Option, register

STATUSES = {
    "NOT_YET_RECRUITING": "Not yet recruiting",
    "RECRUITING": "Recruiting",
    "ENROLLING_BY_INVITATION": "Enrolling by invitation",
    "ACTIVE_NOT_RECRUITING": "Active, not recruiting",
    "SUSPENDED": "Suspended",
    "COMPLETED": "Completed",
    "TERMINATED": "Terminated",
    "WITHDRAWN": "Withdrawn",
    "UNKNOWN": "Unknown status",
    "AVAILABLE": "Expanded access: available",
    "NO_LONGER_AVAILABLE": "Expanded access: no longer available",
    "TEMPORARILY_NOT_AVAILABLE": "Expanded access: temporarily not available",
    "APPROVED_FOR_MARKETING": "Expanded access: approved for marketing",
    "WITHHELD": "Withheld",
}
PHASES = {
    "EARLY_PHASE1": "Early phase 1",
    "PHASE1": "Phase 1",
    "PHASE2": "Phase 2",
    "PHASE3": "Phase 3",
    "PHASE4": "Phase 4",
    "NA": "Phase not applicable",
}
SPONSOR_CLASSES = {
    "NIH": "NIH",
    "FED": "Other federal agency",
    "OTHER_GOV": "Other government",
    "INDUSTRY": "Industry",
    "NETWORK": "Network or cooperative group",
    "OTHER": "Academic or other organization",
    "INDIV": "Individual",
    "AMBIG": "Unclear",
    "UNKNOWN": "Unknown",
}
INTERVENTIONS = {
    "DRUG": "Drug",
    "BIOLOGICAL": "Biological",
    "DEVICE": "Device",
    "PROCEDURE": "Procedure or surgery",
    "RADIATION": "Radiation",
    "BEHAVIORAL": "Behavioral",
    "GENETIC": "Genetic",
    "DIETARY_SUPPLEMENT": "Dietary supplement",
    "COMBINATION_PRODUCT": "Combination product",
    "DIAGNOSTIC_TEST": "Diagnostic test",
    "OTHER": "Other",
}
CONDITION_LEVEL = Option(
    "level",
    "Conditions as",
    (("area", "Disease areas"), ("condition", "Conditions as registered")),
    "area",
)
DISEASE_BRANCHES = ("C", "F")  # diseases, and mental disorders under psychiatry

STUDY_RULE = (
    "A study counts when it was under way at some time in the period: it started by the end "
    "of the period and was not completed before the period began, by the dates in its "
    "registry record. It counts for a member who is one of its officials, when the link is "
    "confirmed or, unless confirmed only is chosen, a strong suggestion not yet decided. The "
    "organization's studies are every study registered under its names, with its members' "
    "studies. Each study counts once."
)
REGISTRY_CAVEAT = (
    "Status is the registry's overall status, as the sponsor reported it to ClinicalTrials.gov. "
    "It is not the status at any one site and never means that a study recruits here."
)
CTGOV_CAVEAT = (
    "Only registered studies are counted: ClinicalTrials.gov holds most interventional studies "
    "but fewer observational ones."
)


def phase_label(phases: list[str] | None, study_type: str | None) -> str:
    if study_type == "OBSERVATIONAL":
        return "Observational (no phase)"
    if study_type == "EXPANDED_ACCESS":
        return "Expanded access"
    names = [p for p in phases or [] if p in PHASES]
    if not names:
        return "Phase not stated"
    if len(names) == 1:
        return PHASES[names[0]]
    numbers = [PHASES[p].removeprefix("Phase ") for p in names]
    return "Phase " + "/".join(numbers)


@dataclass
class Counted:
    study: dict[str, Any]
    members: set[str] = field(default_factory=set)
    links: list[str] = field(default_factory=list)
    confirmed: bool = False


def counted_studies(inputs: Inputs, request: FigureRequest) -> list[Counted]:
    members = members_from_table(inputs.tables["members"])
    basis = request.options.get("basis", "likely")
    links: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in inputs.rows("study_links"):
        if basis == "confirmed" and not link["confirmed"]:
            continue
        links[link["nct_id"]].append(link)
    organization = request.cohort.get("kind") == "organization"
    period = request.period
    out = []
    for s in inputs.rows("studies"):
        first = s["start_date"].year if s["start_date"] else None
        last = s["completion_date"].year if s["completion_date"] else None
        if (first is not None and first > period.end) or (last is not None and last < period.start):
            continue
        low, high = max(first or period.start, period.start), min(last or period.end, period.end)
        counted = Counted(s)
        for link in links.get(s["nct_id"], ()):
            member = members.get(link["person_id"])
            if member is None:
                continue
            if any(member.counts(y) for y in range(low, high + 1)):
                counted.members.add(member.name)
                counted.links.append(link["assertion_id"])
                counted.confirmed = counted.confirmed or bool(link["confirmed"])
        if counted.members or (organization and s["ours"]):
            out.append(counted)
    return out


STUDY_RECORD_COLUMNS = [
    Column("nct_id", "NCT number", "ref"),
    Column("title", "Title", "text"),
    Column("status", "Registry status", "label"),
    Column("phase", "Phase", "label"),
    Column("sponsor", "Lead sponsor", "text"),
    Column("sponsor_class", "Sponsor class", "label"),
    Column("start_date", "Start", "date"),
    Column("completion_date", "Completion", "date"),
    Column("enrollment", "Enrollment", "count"),
    Column("members", "Members", "list"),
    Column("links", "Links", "list"),
]


def study_records(counted: list[Counted]) -> list[dict[str, Any]]:
    out = []
    for c in sorted(counted, key=lambda c: c.study["nct_id"]):
        s = c.study
        out.append(
            {
                "nct_id": s["nct_id"], "title": s["title"],
                "status": STATUSES.get(s["status"] or "", s["status"]),
                "phase": phase_label(s["phases"], s["study_type"]), "sponsor": s["sponsor"],
                "sponsor_class": SPONSOR_CLASSES.get(s["sponsor_class"] or "", s["sponsor_class"]),
                "start_date": s["start_date"].isoformat() if s["start_date"] else None,
                "completion_date": s["completion_date"].isoformat()
                if s["completion_date"] else None,
                "enrollment": s["enrollment"],  # planned or actual, as registered
                "members": sorted(c.members), "links": sorted(c.links),
            }
        )  # fmt: skip
    return out


def _notes(counted: list[Counted], request: FigureRequest) -> list[str]:
    notes = ["Registry status as reported to ClinicalTrials.gov, not local recruitment."]
    if request.cohort.get("kind") == "organization":
        notes.append("The organization's studies are those registered under its names, with "
                     "its members' studies.")  # fmt: skip
    elif request.options.get("basis") == "confirmed":
        notes.append("Only confirmed links to studies are counted.")
    else:
        likely = sum(1 for c in counted if c.members and not c.confirmed)
        if likely:
            notes.append(f"{likely:,} studies count through strong suggestions nobody has "
                         "confirmed yet.")  # fmt: skip
    return notes


def _categories(
    key_of: Any, label: str, *, multi: bool = False, order: dict[str, int] | None = None,
    extra_note: str | None = None,
) -> Any:  # fmt: skip
    def compute(inputs: Inputs, request: FigureRequest) -> Figure:
        counted = counted_studies(inputs, request)
        counts: Counter[str] = Counter()
        for c in counted:
            keys = key_of(c.study, inputs, request)
            for k in set(keys) if multi else keys[:1]:
                counts[k] += 1
        ranked = sorted(counts, key=lambda k: ((order or {}).get(k, 999), -counts[k], k))
        rows = [
            {"label": k, "studies": counts[k], "share": share(counts[k], len(counted))}
            for k in ranked
        ]
        notes = _notes(counted, request)
        if multi:
            notes.append("A study can count in more than one row, so shares add up to more than "
                         "the whole.")  # fmt: skip
        if extra_note:
            notes.append(extra_note)
        return Figure(
            request,
            [Column("label", label, "label"), Column("studies", "Studies", "count", "studies"),
             Column("share", "Share of studies", "share")],
            rows, totals={"studies": len(counted)}, notes=notes,
            record_columns=STUDY_RECORD_COLUMNS, records=study_records(counted),
        )  # fmt: skip

    return compute


def _status(study: dict[str, Any], *_: Any) -> list[str]:
    return [STATUSES.get(study["status"] or "", study["status"] or "Not stated")]


def _phase(study: dict[str, Any], *_: Any) -> list[str]:
    return [phase_label(study["phases"], study["study_type"])]


def _sponsor(study: dict[str, Any], *_: Any) -> list[str]:
    return [SPONSOR_CLASSES.get(study["sponsor_class"] or "", study["sponsor_class"] or "Unknown")]


def _intervention(study: dict[str, Any], *_: Any) -> list[str]:
    types = [INTERVENTIONS.get(t, t) for t in study["intervention_types"] or [] if t]
    return types or ["None registered"]


def _condition(study: dict[str, Any], inputs: Inputs, request: FigureRequest) -> list[str]:
    if request.options["level"] == "condition":
        return list(study["conditions"] or []) or ["None registered"]
    areas: dict[str, list[str]] = defaultdict(list)
    for r in inputs.rows("mesh_areas"):
        if r["area_tree"][0] in DISEASE_BRANCHES:
            areas[r["descriptor_ui"]].append(r["area"])
    found = sorted({a for ui in study["condition_ui"] or [] for a in areas.get(ui, ())})
    return found or ["Not mapped to a disease area"]


def _by_start(inputs: Inputs, request: FigureRequest) -> Figure:
    counted = counted_studies(inputs, request)
    rows = []
    for year in request.period.years:
        here = [c for c in counted if c.study["start_date"] and c.study["start_date"].year == year]
        interventional = sum(1 for c in here if c.study["study_type"] == "INTERVENTIONAL")
        rows.append({"year": year, "studies": len(here), "interventional": interventional,
                     "other": len(here) - interventional})  # fmt: skip
    started = sum(r["studies"] for r in rows)
    notes = _notes(counted, request)
    notes.append(f"{started:,} of the {len(counted):,} studies under way in the period started in "
                 "it; the rest started earlier or give no start date.")  # fmt: skip
    return Figure(
        request,
        [Column("year", "Start year", "year"), Column("studies", "Studies", "count", "studies"),
         Column("interventional", "Interventional", "count", "studies"),
         Column("other", "Observational and other", "count", "studies")],
        rows, totals={"studies": len(counted), "started": started}, notes=notes,
        record_columns=STUDY_RECORD_COLUMNS, records=study_records(counted),
    )  # fmt: skip


COMMON: dict[str, Any] = {
    "area": "Studies",
    "unit": "studies",
    "sources": ("ctgov",),
    "options": (BASIS,),
    "caveats": (REGISTRY_CAVEAT, CTGOV_CAVEAT),
    "needs": ("study_links", "studies"),
}
COUNTING = f"{STUDY_RULE} {WINDOW_RULE}"

register(Metric(
    id="studies.by_status", version=1, name="Studies by registry status",
    definition="The cohort's studies under way in the period, by their overall status in the "
    "registry (recruiting, active, completed, and so on).",
    counting=COUNTING, compute=_categories(_status, "Registry status", order={
        label: i for i, label in enumerate(STATUSES.values())}), **COMMON,
))  # fmt: skip
register(Metric(
    id="studies.by_phase", version=1, name="Studies by phase",
    definition="The cohort's studies under way in the period, by phase; observational studies "
    "have none.",
    counting=COUNTING, compute=_categories(_phase, "Phase"), **COMMON,
))  # fmt: skip
register(Metric(
    id="studies.by_sponsor", version=1, name="Studies by sponsor class",
    definition="The cohort's studies under way in the period, by the class of their lead "
    "sponsor: NIH, other federal, industry, academic, and so on.",
    counting=COUNTING, compute=_categories(_sponsor, "Lead sponsor class"), **COMMON,
))  # fmt: skip
register(Metric(
    id="studies.by_intervention", version=1, name="Studies by intervention type",
    definition="The cohort's studies under way in the period, by the types of intervention "
    "they test.",
    counting=f"{COUNTING} A study with interventions of several types counts once for each type.",
    compute=_categories(_intervention, "Intervention type", multi=True), **COMMON,
))  # fmt: skip
register(Metric(
    id="studies.by_condition", version=1, name="Studies by condition",
    definition="The cohort's studies under way in the period, by the conditions they study, "
    "as disease areas (the tops of MeSH's disease branches) or as registered.",
    counting=f"{COUNTING} A study counts once for each disease area its conditions fall in, "
    "using the MeSH headings the registry assigns to them.",
    compute=_categories(_condition, "Condition", multi=True),
    **{**COMMON, "sources": ("ctgov", "mesh"), "options": (BASIS, CONDITION_LEVEL),
       "needs": ("study_links", "studies", "mesh_areas")},
))  # fmt: skip
register(Metric(
    id="studies.by_start_year", version=1, name="Studies by start year",
    definition="The cohort's studies that started in each year of the period, interventional "
    "and other.",
    counting=COUNTING, compute=_by_start, **COMMON,
))  # fmt: skip

__all__ = ["INTERVENTIONS", "PHASES", "SPONSOR_CLASSES", "STATUSES", "counted_studies",
           "phase_label"]  # fmt: skip
