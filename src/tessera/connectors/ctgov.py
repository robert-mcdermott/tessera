"""ClinicalTrials.gov API v2: public registry records for studies.

Modes:
  org    studies whose lead sponsor, collaborators, sites, or official affiliations match
         the organization profile
  query  any Essie expression (``query.term``)
  ids    specific NCT IDs (former IDs resolve to the current record)

Contact details (names, emails, and phone numbers of central and site contacts) are
removed from responses before they are stored anywhere, including the raw zone.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

from tessera.config import Config
from tessera.connectors.base import Connector, FetchReport, IngestOptions, Payload, Plan, Records
from tessera.connectors.grants import normalize_grant
from tessera.connectors.http import SourceError
from tessera.core.clock import utc_now
from tessera.core.errors import TesseraError

PAGE_SIZE = 1000
ID_BATCH = 100
FIELDS = "ProtocolSection,DerivedSection,HasResults"
NCT_ID = re.compile(r"^NCT\d{8}$")
ORG_AREAS = (
    "LeadSponsorName",
    "CollaboratorName",
    "LocationFacility",
    "OverallOfficialAffiliation",
)


class CtgovConnector(Connector):
    name = "ctgov"
    label = "ClinicalTrials.gov"
    datasets = ("ctgov",)
    base_url = "https://clinicaltrials.gov/api/v2/"
    min_interval = 1.2  # about 50 requests a minute
    modes = ("org", "query", "ids")

    def plan(self, options: IngestOptions, checkpoint: str | None, today: date) -> Plan:
        mode = options.mode
        if mode == "auto":
            mode = "ids" if options.ids else "query" if options.query else "org"
        if mode == "ids":
            ids = normalize_nct_ids(options.ids)
            return Plan(mode, "", f"{len(ids)} NCT IDs", params={"ids": ids})
        if mode not in {"org", "query"}:
            raise TesseraError(
                f"ClinicalTrials.gov has no mode {mode!r}; choose from {', '.join(self.modes)}"
            )
        term = (options.query or "").strip() if mode == "query" else org_expression(self.config)
        if not term:
            raise TesseraError("A ClinicalTrials.gov query is required (--query).")
        since = options.since
        if since is None and checkpoint and not options.full:
            since = date.fromisoformat(checkpoint) - timedelta(days=1)
        end = options.until or today
        advanced = None
        if since or options.until:
            low = since.isoformat() if since else "MIN"
            advanced = f"AREA[LastUpdatePostDate]RANGE[{low},{end.isoformat()}]"
        scope = f"{mode}:{json.dumps(term)[:180]}"
        watermark = None if options.max_results else end.isoformat()
        description = term + (f" (updated {since} to {end})" if since else "")
        params = {"term": term, "advanced": advanced, "max_results": options.max_results}
        return Plan(mode, scope, description, new_watermark=watermark, params=params)

    def fetch(self, plan: Plan, report: FetchReport) -> Iterator[Payload]:
        try:
            report.data_version = self.http.get("version").json().get("dataTimestamp")
        except (SourceError, ValueError) as exc:
            report.note(f"ClinicalTrials.gov data version unavailable: {exc}")
        if plan.mode == "ids":
            yield from self._fetch_ids(plan.params["ids"], report)
            return
        params: dict[str, Any] = {"query.term": plan.params["term"], "fields": FIELDS}
        if plan.params.get("advanced"):
            params["filter.advanced"] = plan.params["advanced"]
        cap = plan.params.get("max_results")
        remaining = cap
        token: str | None = None
        page = 0
        while remaining is None or remaining > 0:
            page += 1
            page_params = {**params, "pageSize": min(PAGE_SIZE, remaining or PAGE_SIZE)}
            if token:
                page_params["pageToken"] = token
            else:
                page_params["countTotal"] = "true"
            try:
                data = self.http.get("studies", params=page_params).json()
            except (SourceError, ValueError) as exc:
                if page == 1:
                    raise
                report.fail({"page": page}, exc)
                return
            if page == 1:
                report.total_found = int(data.get("totalCount") or 0)
                report.expected = (
                    report.total_found if cap is None else min(cap, report.total_found)
                )
                report.truncated = report.expected < report.total_found
            studies = data.get("studies") or []
            if remaining is not None:
                studies = studies[:remaining]
                remaining -= len(studies)
            report.payloads += 1
            yield _payload(studies, {"page": page})
            token = data.get("nextPageToken")
            if not token:
                break

    def _fetch_ids(self, ids: list[str], report: FetchReport) -> Iterator[Payload]:
        report.total_found = report.expected = len(ids)
        found: set[str] = set()
        for start in range(0, len(ids), ID_BATCH):
            chunk = ids[start : start + ID_BATCH]
            params: dict[str, Any] = {
                "filter.ids": ",".join(chunk),
                "pageSize": PAGE_SIZE,
                "fields": FIELDS,
            }
            studies: list[dict[str, Any]] = []
            try:
                while True:
                    data = self.http.get("studies", params=params).json()
                    studies.extend(data.get("studies") or [])
                    if not data.get("nextPageToken"):
                        break
                    params["pageToken"] = data["nextPageToken"]
            except (SourceError, ValueError) as exc:
                report.fail({"ids": [chunk[0], chunk[-1]]}, exc)
                continue
            for study in studies:
                ident = (study.get("protocolSection") or {}).get("identificationModule") or {}
                found.update([ident.get("nctId"), *(ident.get("nctIdAliases") or [])])
            report.payloads += 1
            yield _payload(studies, {"ids": [chunk[0], chunk[-1]]})
        missing = [i for i in ids if i not in found]
        if missing:
            report.note(
                f"ClinicalTrials.gov: {len(missing)} NCT {'ID' if len(missing) == 1 else 'IDs'} "
                f"not found: {', '.join(missing[:10])}"
            )

    @classmethod
    def parse(cls, payload: Payload) -> Records:
        data = payload.json()
        studies = (data.get("studies") or []) if isinstance(data, dict) else []
        return {"ctgov": [parse_study(s) for s in studies if _nct(s)]}


# --- helpers ------------------------------------------------------------------------------


def _payload(studies: list[dict[str, Any]], request: dict[str, Any]) -> Payload:
    body = json.dumps({"studies": [scrub_contacts(s) for s in studies]}, separators=(",", ":"))
    return Payload("studies", body.encode("utf-8"), "json", utc_now(), request=request)


def scrub_contacts(study: dict[str, Any]) -> dict[str, Any]:
    """Remove central and site contacts (names, emails, phones) from a study, in place."""
    module = (study.get("protocolSection") or {}).get("contactsLocationsModule")
    if isinstance(module, dict):
        module.pop("centralContacts", None)
        for location in module.get("locations") or []:
            if isinstance(location, dict):
                location.pop("contacts", None)
        for official in module.get("overallOfficials") or []:
            if isinstance(official, dict):
                for key in ("email", "phone", "phoneExt"):
                    official.pop(key, None)
    return study


def normalize_nct_ids(values: list[str]) -> list[str]:
    ids = [str(v).strip().upper() for v in values if str(v).strip()]
    bad = [i for i in ids if not NCT_ID.match(i)]
    if bad:
        raise TesseraError(f"Invalid NCT ID(s): {', '.join(bad[:5])} (expected NCT + 8 digits)")
    if not ids:
        raise TesseraError("At least one NCT ID is required (--ids).")
    return list(dict.fromkeys(ids))


def org_expression(config: Config) -> str:
    org = config.organization
    names = list(dict.fromkeys(org.ctgov_org_names or org.aliases))
    if not names:
        raise TesseraError("organization.ctgov_org_names (or aliases) is needed for a study sweep.")
    quoted = " OR ".join('"' + n.replace('"', " ").strip() + '"' for n in names)
    return " OR ".join(f"AREA[{area}]({quoted})" for area in ORG_AREAS)


def _nct(study: dict[str, Any]) -> str | None:
    return ((study.get("protocolSection") or {}).get("identificationModule") or {}).get("nctId")


def _opt(value: Any) -> Any:
    return None if value in (None, "") else value


def _date(value: Any) -> date | None:
    """ "2024-03" or "2024-03-15" (registry dates may lack a day) as a date."""
    if not isinstance(value, str) or not value:
        return None
    parts = value.split("-")
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        day = int(parts[2]) if len(parts) > 2 else 1
        return date(year, month, day)
    except (ValueError, IndexError):
        return None


def _date_struct(struct: dict[str, Any] | None) -> tuple[date | None, str | None]:
    struct = struct or {}
    return _date(struct.get("date")), _opt(struct.get("type"))


def _meshes(module: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [
        {"id": m.get("id"), "term": m.get("term")}
        for m in (module or {}).get("meshes") or []
        if m.get("id") or m.get("term")
    ]


def parse_study(raw: dict[str, Any]) -> dict[str, Any]:
    protocol = raw.get("protocolSection") or {}
    ident = protocol.get("identificationModule") or {}
    status = protocol.get("statusModule") or {}
    sponsors = protocol.get("sponsorCollaboratorsModule") or {}
    description = protocol.get("descriptionModule") or {}
    conditions = protocol.get("conditionsModule") or {}
    design = protocol.get("designModule") or {}
    enrollment = design.get("enrollmentInfo") or {}
    arms = protocol.get("armsInterventionsModule") or {}
    contacts = protocol.get("contactsLocationsModule") or {}
    references = protocol.get("referencesModule") or {}
    derived = raw.get("derivedSection") or {}
    lead = sponsors.get("leadSponsor") or {}

    start, start_type = _date_struct(status.get("startDateStruct"))
    primary, primary_type = _date_struct(status.get("primaryCompletionDateStruct"))
    completion, completion_type = _date_struct(status.get("completionDateStruct"))

    refs: dict[str, dict[str, Any]] = {}
    for ref in references.get("references") or []:
        pmid = str(ref.get("pmid") or "").strip()
        if pmid.isdigit() and pmid not in refs:
            refs[pmid] = {
                "pmid": pmid,
                "type": _opt(ref.get("type")),
                "citation": _opt(ref.get("citation")),
            }

    secondary = []
    for seq, item in enumerate(ident.get("secondaryIdInfos") or [], 1):
        kind = _opt(item.get("type"))
        core, ic_serial = normalize_grant(item.get("id"), "NIH" if kind == "NIH" else None)
        if kind != "NIH" and core is None:  # elsewhere, only full grant numbers are trusted
            ic_serial = None
        secondary.append(
            {
                "seq": seq,
                "id": _opt(item.get("id")),
                "type": kind,
                "core_project_num": core,
                "ic_serial": ic_serial,
            }
        )

    return {
        "nct_id": ident["nctId"],
        "aliases": list(ident.get("nctIdAliases") or []),
        "org_study_id": _opt((ident.get("orgStudyIdInfo") or {}).get("id")),
        "brief_title": _opt(ident.get("briefTitle")),
        "official_title": _opt(ident.get("officialTitle")),
        "acronym": _opt(ident.get("acronym")),
        "overall_status": _opt(status.get("overallStatus")),
        "last_known_status": _opt(status.get("lastKnownStatus")),
        "why_stopped": _opt(status.get("whyStopped")),
        "study_type": _opt(design.get("studyType")),
        "phases": list(design.get("phases") or []),
        "conditions": list(conditions.get("conditions") or []),
        "condition_mesh": _meshes(derived.get("conditionBrowseModule")),
        "intervention_mesh": _meshes(derived.get("interventionBrowseModule")),
        "interventions": [
            {"type": _opt(i.get("type")), "name": _opt(i.get("name"))}
            for i in arms.get("interventions") or []
        ],
        "keywords": list(conditions.get("keywords") or []),
        "lead_sponsor": _opt(lead.get("name")),
        "lead_sponsor_class": _opt(lead.get("class")),
        "collaborators": [c["name"] for c in sponsors.get("collaborators") or [] if c.get("name")],
        "start_date": start,
        "start_date_type": start_type,
        "primary_completion_date": primary,
        "primary_completion_date_type": primary_type,
        "completion_date": completion,
        "completion_date_type": completion_type,
        "enrollment": enrollment.get("count") if isinstance(enrollment.get("count"), int) else None,
        "enrollment_type": _opt(enrollment.get("type")),
        "has_results": bool(raw.get("hasResults")),
        "first_posted": _date_struct(status.get("studyFirstPostDateStruct"))[0],
        "last_update_posted": _date_struct(status.get("lastUpdatePostDateStruct"))[0],
        "brief_summary": _opt(description.get("briefSummary")),
        "officials": [
            {
                "seq": seq,
                "name": _opt(o.get("name")),
                "affiliation": _opt(o.get("affiliation")),
                "role": _opt(o.get("role")),
            }
            for seq, o in enumerate(contacts.get("overallOfficials") or [], 1)
        ],
        "sites": [
            {
                "seq": seq,
                "facility": _opt(loc.get("facility")),
                "status": _opt(loc.get("status")),
                "city": _opt(loc.get("city")),
                "state": _opt(loc.get("state")),
                "country": _opt(loc.get("country")),
                "latitude": (loc.get("geoPoint") or {}).get("lat"),
                "longitude": (loc.get("geoPoint") or {}).get("lon"),
            }
            for seq, loc in enumerate(contacts.get("locations") or [], 1)
        ],
        "references": list(refs.values()),
        "secondary_ids": secondary,
    }
