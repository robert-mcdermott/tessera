"""NIH RePORTER API v2: projects (one record per project per fiscal year) and the
publication link table (core project number <-> PMID).

Modes:
  org    projects at the organization's configured names, by fiscal year
  pi     all projects for given investigator profile IDs (any organization)
  ids    specific application IDs
  query  raw RePORTER criteria as JSON, checked against the criteria the API accepts

After the projects, the links for every core project seen are fetched as well.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from datetime import date
from typing import Any

from tessera.connectors.base import Connector, FetchReport, IngestOptions, Payload, Plan, Records
from tessera.connectors.http import SourceError
from tessera.core.clock import utc_now
from tessera.core.errors import TesseraError

log = logging.getLogger("tessera.connectors.reporter")

PAGE_SIZE = 500
SEARCH_LIMIT = 15_000  # offset + limit may not exceed this for one search
LINK_LIMIT = 10_000  # links served for one publications search
LINK_BATCH = 100  # core project numbers per publications request

NIH_ICS = (
    "CLC", "FIC", "NCATS", "NCCIH", "NCI", "NEI", "NHGRI", "NHLBI", "NIA", "NIAAA", "NIAID",
    "NIAMS", "NIBIB", "NICHD", "NIDA", "NIDCD", "NIDCR", "NIDDK", "NIEHS", "NIGMS", "NIMH",
    "NIMHD", "NINDS", "NINR", "NLM", "OD", "CSR", "NCRR",
)  # fmt: skip
CDC_CENTERS = (
    "ATSDR", "CGH", "NCBDDD", "NCCDPHP", "NCEH", "NCEZID", "NCHHSTP", "NCHS", "NCIPC",
    "NCIRD", "NIOSH", "CID", "NCHM", "ODCDC", "PHPPO",
)  # fmt: skip
HRSA_DIVISIONS = ("DADHP", "DDA", "DM,BHP", "DN")
OTHER_AGENCIES = ("AHRQ", "FDA", "VA", "SAMHSA", "OMHHE")
ADMIN_AGENCIES = (*NIH_ICS, *CDC_CENTERS, *HRSA_DIVISIONS, *OTHER_AGENCIES)
AGENCY_GROUPS = {"NIH": NIH_ICS, "ALLCDC": CDC_CENTERS, "HRSA": HRSA_DIVISIONS}
# Small enough to fetch whole within a fiscal year; catches centers with unpublished codes.
CATCH_ALL_AGENCIES = ("ALLCDC", "HRSA")

# The API silently ignores unknown criteria (and then matches every project), so raw
# criteria are checked against this list first.
CRITERIA_KEYS = frozenset(
    {
        "use_relevance", "fiscal_years", "include_active_projects", "pi_names", "po_names",
        "org_names", "org_names_exact_match", "pi_profile_ids", "org_cities", "org_states",
        "project_nums", "org_countries", "appl_ids", "project_num_split", "agencies",
        "is_agency_admin", "is_agency_funding", "activity_codes", "cooperative_agreement_codes",
        "award_types", "dept_types", "cong_dists", "foa", "opportunity_numbers",
        "spending_categories", "project_start_date", "project_end_date", "date_added",
        "organization_type", "full_study_sections", "award_notice_date", "award_amount_range",
        "exclude_subprojects", "multi_pi_only", "newly_added_projects_only", "sub_project_only",
        "funding_mechanism", "covid_response", "outcomes_only", "advanced_text_search",
    }
)  # fmt: skip


def fiscal_year(day: date) -> int:
    """US federal fiscal years start on 1 October."""
    return day.year + 1 if day.month >= 10 else day.year


class ReporterConnector(Connector):
    name = "reporter"
    label = "NIH RePORTER"
    datasets = ("reporter", "reporter_links")
    base_url = "https://api.reporter.nih.gov/v2/"
    min_interval = 1.0  # NIH asks for at most one request per second
    modes = ("org", "pi", "ids", "query")

    # -- planning ------------------------------------------------------------------------

    def plan(self, options: IngestOptions, checkpoint: str | None, today: date) -> Plan:
        mode = options.mode
        if mode == "auto":
            mode = "ids" if options.ids else "query" if options.query else "org"
        current = fiscal_year(today)
        if mode == "ids":
            ids = _positive_ints(options.ids, "application ID")
            return Plan(
                mode, "", f"{len(ids)} application IDs", params={"criteria": {"appl_ids": ids}}
            )
        if mode == "pi":
            ids = _positive_ints(options.ids, "profile ID")
            criteria = {"pi_profile_ids": ids}
            return Plan(
                mode, "", f"projects of {len(ids)} investigators", params={"criteria": criteria}
            )
        if mode == "query":
            criteria = parse_criteria(options.query or "")
            return Plan(
                mode,
                "",
                json.dumps(criteria),
                params={"criteria": criteria, "max_results": options.max_results},
            )
        if mode != "org":
            raise TesseraError(
                f"RePORTER has no mode {mode!r}; choose from {', '.join(self.modes)}"
            )
        names = list(self.config.organization.reporter_org_names)
        if not names:
            raise TesseraError("organization.reporter_org_names is needed for a RePORTER sweep.")
        if options.since:
            first = fiscal_year(options.since)
        elif checkpoint and not options.full:
            # Amounts and dates of recent awards keep changing, so re-read recent years.
            first = min(int(checkpoint), current - 1)
        else:
            first = current - self.config.sync.window_years
        last = fiscal_year(options.until) if options.until else current + 1
        years = list(range(first, last + 1))
        criteria = {"org_names": names, "fiscal_years": years}
        watermark = None if options.max_results else str(current)
        description = f"{', '.join(names)}: fiscal years {years[0]}-{years[-1]}"
        return Plan(
            "org",
            "org:" + "|".join(sorted(names)),
            description,
            new_watermark=watermark,
            params={"criteria": criteria, "max_results": options.max_results},
        )

    # -- fetching ------------------------------------------------------------------------

    def fetch(self, plan: Plan, report: FetchReport) -> Iterator[Payload]:
        report.data_version = self._latest_update(report)
        criteria = plan.params["criteria"]
        cap = plan.params.get("max_results")
        cores: dict[str, None] = {}
        if "appl_ids" in criteria:
            ids = criteria["appl_ids"]
            report.total_found = report.expected = len(ids)
            for start in range(0, len(ids), PAGE_SIZE):
                part = {"appl_ids": ids[start : start + PAGE_SIZE]}
                yield from self._pages(part, PAGE_SIZE, report, cores, where={"ids_from": start})
        else:
            total = self._count(criteria)
            report.total_found = total
            parts = [(criteria, total)]
            if total > SEARCH_LIMIT and (cap is None or cap > SEARCH_LIMIT):
                parts = self._split(criteria, total, report)
            reachable = sum(min(n, SEARCH_LIMIT) for _, n in parts)
            report.expected = min(total, reachable, cap if cap is not None else total)
            if report.expected < total:
                report.truncated = True
            remaining = report.expected
            for number, (part, count) in enumerate(parts, 1):
                if remaining <= 0:
                    break
                limit = min(count, SEARCH_LIMIT, remaining)
                remaining -= limit
                yield from self._pages(part, limit, report, cores, where={"part": number})
        yield from self._links(list(cores), report)

    def _search(self, criteria: dict[str, Any], offset: int, limit: int, **extra: Any) -> dict:
        body = {"criteria": criteria, "offset": offset, "limit": limit, **extra}
        response = self.http.post("projects/search", json=body)
        data = response.json()
        if not isinstance(data, dict):
            raise SourceError(self.name, f"unexpected response: {str(data)[:200]}")
        return data

    def _count(self, criteria: dict[str, Any]) -> int:
        data = self._search(criteria, 0, 1, include_fields=["ApplId"])
        return int((data.get("meta") or {}).get("total") or 0)

    def _latest_update(self, report: FetchReport) -> str | None:
        try:
            data = self._search(
                {},
                0,
                1,
                include_fields=["ApplId", "DateAdded"],
                sort_field="date_added",
                sort_order="desc",
            )
        except (SourceError, ValueError) as exc:
            report.note(f"RePORTER data version unavailable: {exc}")
            return None
        results = data.get("results") or []
        return results[0].get("date_added") if results else None

    def _pages(
        self,
        criteria: dict[str, Any],
        limit: int,
        report: FetchReport,
        cores: dict[str, None],
        where: dict[str, Any],
    ) -> Iterator[Payload]:
        offset = 0
        while offset < limit:
            size = min(PAGE_SIZE, limit - offset)
            try:
                response = self.http.post(
                    "projects/search",
                    json={
                        "criteria": criteria,
                        "offset": offset,
                        "limit": size,
                        "sort_field": "appl_id",
                        "sort_order": "asc",
                    },
                )
                results = response.json().get("results") or []
            except (SourceError, ValueError, AttributeError) as exc:
                report.fail({**where, "offset": offset}, exc)
                return
            for project in results:
                if core := (project.get("core_project_num") or "").strip():
                    cores.setdefault(core.upper())
            report.payloads += 1
            yield Payload(
                "projects",
                response.content,
                "json",
                utc_now(),
                request={**where, "offset": offset, "criteria": criteria},
            )
            offset += size
            if len(results) < size:
                break

    def _split(
        self, criteria: dict[str, Any], total: int, report: FetchReport
    ) -> list[tuple[dict[str, Any], int]]:
        """Partition into sub-searches of at most SEARCH_LIMIT: by fiscal year, then agency."""
        parts: list[tuple[dict[str, Any], int]] = []
        pending = [(criteria, total)]
        while pending:
            part, count = pending.pop(0)
            split = self._children(part) if count > SEARCH_LIMIT else None
            if not split:
                parts.append((part, count))
                continue
            key, values = split
            counted = [
                ({**part, key: value}, self._count({**part, key: value})) for value in values
            ]
            union = {**part, key: [v for value in values for v in value]}
            covered = self._count(union) if key == "agencies" else sum(n for _, n in counted)
            if covered < count:
                report.note(f"RePORTER: splitting left {count - covered:,} projects unreachable.")
            pending[:0] = [(child, n) for child, n in counted if n]
        return parts

    @staticmethod
    def _children(criteria: dict[str, Any]) -> tuple[str, list[list[Any]]] | None:
        years = criteria.get("fiscal_years") or []
        if len(years) > 1 and not criteria.get("include_active_projects"):
            return "fiscal_years", [[year] for year in years]
        agencies = criteria.get("agencies") or [*ADMIN_AGENCIES, *CATCH_ALL_AGENCIES]
        if len(agencies) == 1:
            agencies = list(AGENCY_GROUPS.get(agencies[0], ()))
        if len(agencies) > 1:
            return "agencies", [[agency] for agency in agencies]
        return None

    # -- publication links -----------------------------------------------------------------

    def _links(self, cores: list[str], report: FetchReport) -> Iterator[Payload]:
        for start in range(0, len(cores), LINK_BATCH):
            yield from self._link_batch(cores[start : start + LINK_BATCH], report, quick=False)

    def _link_batch(self, cores: list[str], report: FetchReport, quick: bool) -> Iterator[Payload]:
        """All link pages for a batch; halves batches over the limit, narrows down failures."""
        try:
            pages = self._link_pages(cores, quick)
        except (SourceError, ValueError, AttributeError) as exc:
            if len(cores) == 1:
                report.fail({"core_project_num": cores[0], "links": True}, exc)
                return
            half = len(cores) // 2  # one broken project fails the whole batch; isolate it
            yield from self._link_batch(cores[:half], report, quick=True)
            yield from self._link_batch(cores[half:], report, quick=True)
            return
        if pages is None:  # over the link limit: halve
            half = len(cores) // 2
            yield from self._link_batch(cores[:half], report, quick)
            yield from self._link_batch(cores[half:], report, quick)
            return
        for content, offset in pages:
            report.payloads += 1
            yield Payload(
                "links",
                content,
                "json",
                utc_now(),
                request={"cores": [cores[0], cores[-1]], "count": len(cores), "offset": offset},
            )

    def _link_pages(self, cores: list[str], quick: bool) -> list[tuple[bytes, int]] | None:
        retries = 0 if quick else None
        body: dict[str, Any] = {
            "criteria": {"core_project_nums": cores},
            "offset": 0,
            "limit": PAGE_SIZE,
        }
        first = self.http.post("publications/search", json=body, retries=retries)
        data = first.json()
        total = int((data.get("meta") or {}).get("total") or 0)
        if total > LINK_LIMIT and len(cores) > 1:
            return None
        pages = [(first.content, 0)]
        offset = len(data.get("results") or [])
        while data.get("results") and offset < min(total, LINK_LIMIT):
            response = self.http.post(
                "publications/search", json={**body, "offset": offset}, retries=retries
            )
            data = response.json()
            if not data.get("results"):
                break
            pages.append((response.content, offset))
            offset += len(data["results"])
        return pages

    # -- parsing -------------------------------------------------------------------------

    @classmethod
    def parse(cls, payload: Payload) -> Records:
        data = payload.json()
        results = (data.get("results") or []) if isinstance(data, dict) else []
        if payload.kind == "links":
            links: dict[tuple[str, str], dict[str, Any]] = {}
            for item in results:
                pmid, core = item.get("pmid"), (item.get("coreproject") or "").strip().upper()
                if pmid and core:
                    links[(str(pmid), core)] = {
                        "pmid": str(pmid),
                        "core_project_num": core,
                        "appl_id": item.get("applid"),
                    }
            return {"reporter_links": list(links.values())}
        return {"reporter": [parse_project(p) for p in results if p.get("appl_id")]}


# --- record parsing -------------------------------------------------------------------------

_TERM = re.compile(r"<([^<>]*)>")


def _opt(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
    return None if value in (None, "") else value


def _name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return " ".join(value.split()) or None


def _date(value: Any) -> date | None:
    value = _opt(value)
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _amount(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return round(value)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))


def _terms(raw: dict[str, Any]) -> list[str]:
    preferred = raw.get("pref_terms")
    if isinstance(preferred, str) and preferred.strip():
        return _unique([t.strip() for t in preferred.split(";")])
    terms = raw.get("terms")
    return _unique([t.strip() for t in _TERM.findall(terms)]) if isinstance(terms, str) else []


def _spending(raw: dict[str, Any]) -> list[str]:
    desc = raw.get("spending_categories_desc")
    return _unique([t.strip() for t in desc.split(";")]) if isinstance(desc, str) else []


def parse_project(raw: dict[str, Any]) -> dict[str, Any]:
    org = raw.get("organization") or {}
    admin = raw.get("agency_ic_admin") or {}
    split = raw.get("project_num_split") or {}
    ueis = [u for u in org.get("org_ueis") or [] if u]
    appl_id = int(raw["appl_id"])
    investigators = []
    for pi in raw.get("principal_investigators") or []:
        profile = pi.get("profile_id")
        if not isinstance(profile, int) or isinstance(profile, bool):
            continue
        first, middle, last = (_name(pi.get(k)) for k in ("first_name", "middle_name", "last_name"))
        investigators.append(
            {
                "profile_id": profile,
                "full_name": _name(pi.get("full_name"))
                or _name(" ".join(n for n in (first, middle, last) if n)),
                "family": last,
                "given": first,
                "middle": middle,
                "title": _opt(pi.get("title")),
                "is_contact_pi": bool(pi.get("is_contact_pi")),
            }
        )
    core = _opt(raw.get("core_project_num"))
    return {
        "appl_id": appl_id,
        "project_num": _opt(raw.get("project_num")),
        "core_project_num": core.upper() if isinstance(core, str) else None,
        "subproject_id": _opt(str(raw["subproject_id"])) if raw.get("subproject_id") else None,
        "fiscal_year": raw.get("fiscal_year"),
        "title": _opt(raw.get("project_title")),
        "abstract": _opt(raw.get("abstract_text")),
        "phr": _opt(raw.get("phr_text")),
        "activity_code": _opt(raw.get("activity_code")) or _opt(split.get("activity_code")),
        "agency_ic_admin": _opt(admin.get("abbreviation")) or _opt(admin.get("code")),
        "funding_mechanism": _opt(raw.get("funding_mechanism")),
        "award_amount": _amount(raw.get("award_amount")),
        "direct_cost": _amount(raw.get("direct_cost_amt")),
        "indirect_cost": _amount(raw.get("indirect_cost_amt")),
        "award_notice_date": _date(raw.get("award_notice_date")),
        "project_start": _date(raw.get("project_start_date")),
        "project_end": _date(raw.get("project_end_date")),
        "budget_start": _date(raw.get("budget_start")),
        "budget_end": _date(raw.get("budget_end")),
        "is_active": raw.get("is_active") if isinstance(raw.get("is_active"), bool) else None,
        "is_new": raw.get("is_new") if isinstance(raw.get("is_new"), bool) else None,
        "org_name": _name(org.get("org_name")),
        "org_city": _opt(org.get("org_city")) or _opt(org.get("city")),
        "org_state": _opt(org.get("org_state")),
        "org_country": _opt(org.get("org_country")) or _opt(org.get("country")),
        "org_uei": _opt(org.get("primary_uei")) or (ueis[0] if ueis else None),
        "org_dept_type": _opt(org.get("dept_type")),
        "terms": _terms(raw),
        "spending_categories": _spending(raw),
        "date_added": _date(raw.get("date_added")),
        "investigators": investigators,
    }


def parse_criteria(text: str) -> dict[str, Any]:
    try:
        criteria = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TesseraError(f"RePORTER criteria must be a JSON object: {exc}") from exc
    if not isinstance(criteria, dict) or not criteria:
        raise TesseraError("RePORTER criteria must be a non-empty JSON object.")
    unknown = sorted(set(criteria) - CRITERIA_KEYS)
    if unknown:
        raise TesseraError(
            f"Unknown RePORTER criteria {unknown}. The API ignores unknown criteria and would "
            "match every project."
        )
    return criteria


def _positive_ints(values: list[str], label: str) -> list[int]:
    out, bad = [], []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        if text.isdigit() and int(text) > 0:
            out.append(int(text))
        else:
            bad.append(text)
    if bad:
        raise TesseraError(f"Invalid {label}(s): {', '.join(bad[:5])}")
    if not out:
        raise TesseraError(f"At least one {label} is required (--ids).")
    return list(dict.fromkeys(out))
