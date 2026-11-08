"""Public ORCID records for the people in the directory. See spec/data-sources.md.

ORCID records are researchers' own statements: their works (with PubMed IDs and DOIs where
known) and their employment. Tessera reads the public record of each ORCID iD it already
holds for a directory member, one request per iD, never the records of everyone who
appears in PubMed. The public API answers without credentials for this volume of use.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

from tessera.config import Config
from tessera.connectors.base import Connector, FetchReport, IngestOptions, Payload, Plan, Records
from tessera.connectors.http import HttpClient, SourceError
from tessera.connectors.pubmed import ORCID_RE, normalize_orcid
from tessera.core.clock import utc_now

# Answers that mean "no public record to read", not a failed request: the iD does not
# exist, the record was deprecated in favor of another iD, or it was locked or deactivated.
UNAVAILABLE = {404: "not_found", 409: "unavailable", 410: "deactivated"}


def orcid_checksum_ok(orcid: str) -> bool:
    """ISO 7064 MOD 11-2, the check digit at the end of every ORCID iD."""
    digits = orcid.replace("-", "").upper()
    if not re.fullmatch(r"\d{15}[\dX]", digits):
        return False
    total = 0
    for d in digits[:-1]:
        total = (total + int(d)) * 2
    check = (12 - total % 11) % 11
    return digits[-1] == ("X" if check == 10 else str(check))


def valid_orcids(values: list[str]) -> list[str]:
    """Normalized, checksum-valid, de-duplicated iDs in a stable order."""
    out = {o for v in values if (o := normalize_orcid(v)) and orcid_checksum_ok(o)}
    return sorted(out)


class OrcidConnector(Connector):
    name = "orcid"
    label = "ORCID"
    datasets = ("orcid",)
    base_url = "https://pub.orcid.org/v3.0/"
    min_interval = 0.25
    modes = ("ids", "refresh")

    @classmethod
    def make_http(cls, config: Config, **kwargs: Any) -> HttpClient:
        return super().make_http(config, headers={"Accept": "application/json"}, **kwargs)

    def plan(self, options: IngestOptions, checkpoint: str | None, today: date) -> Plan:
        # In "auto" mode the ingest layer supplies the directory's iDs that need reading.
        orcids = valid_orcids(options.ids)
        if options.max_results is not None:
            orcids = orcids[: options.max_results]
        mode = "ids" if options.mode == "ids" else "refresh"
        return Plan(
            mode,
            "",
            f"public records for {len(orcids)} ORCID iDs",
            params={"orcids": orcids},
            empty=not orcids,
        )

    def fetch(self, plan: Plan, report: FetchReport) -> Iterator[Payload]:
        orcids = plan.params["orcids"]
        report.total_found = report.expected = len(orcids)
        for orcid in orcids:
            where = {"orcid": orcid}
            try:
                response = self.http.get(f"{orcid}/record")
                response.json()
            except SourceError as exc:
                if exc.status in UNAVAILABLE:
                    # Recorded as data: the record's status (and, for a deprecated iD, the
                    # primary iD it points to) is useful evidence about identity.
                    body = {"orcid": orcid, "http_status": exc.status, "message": str(exc)}
                    report.payloads += 1
                    yield Payload(
                        "unavailable", json.dumps(body).encode(), "json", utc_now(), request=where
                    )
                    continue
                report.fail(where, exc)
                continue
            except ValueError as exc:
                report.fail(where, exc)
                continue
            report.payloads += 1
            yield Payload("record", response.content, "json", utc_now(), request=where)

    @classmethod
    def parse(cls, payload: Payload) -> Records:
        data = payload.json()
        if payload.kind == "unavailable":
            return {"orcid": [_unavailable(data)]}
        orcid = normalize_orcid(_get(data, "orcid-identifier", "path") or "")
        if not orcid:
            return {"orcid": []}
        person = data.get("person") or {}
        name = person.get("name") or {}
        activities = data.get("activities-summary") or {}
        return {
            "orcid": [
                {
                    "orcid": orcid,
                    "status": "public",
                    "primary_orcid": None,
                    "given_names": _value(name.get("given-names")),
                    "family_name": _value(name.get("family-name")),
                    "credit_name": _value(name.get("credit-name")),
                    "other_names": [
                        v
                        for o in (_get(person, "other-names", "other-name") or [])
                        if (v := (o.get("content") or "").strip())
                    ],
                    "last_modified": _millis(_get(data, "history", "last-modified-date", "value")),
                    "works": _works(orcid, activities),
                    "employments": _employments(orcid, activities),
                }
            ]
        }


def _unavailable(data: dict[str, Any]) -> dict[str, Any]:
    message = data.get("message") or ""
    status = UNAVAILABLE.get(data.get("http_status"), "unavailable")
    primary = None
    if "deprecated" in message.lower():
        status = "deprecated"
        found = [normalize_orcid(m) for m in ORCID_RE.findall(message)]
        primary = next((o for o in found if o and o != data["orcid"]), None)
    elif "locked" in message.lower():
        status = "locked"
    elif "deactivated" in message.lower():
        status = "deactivated"
    return {
        "orcid": data["orcid"],
        "status": status,
        "primary_orcid": primary,
        "given_names": None,
        "family_name": None,
        "credit_name": None,
        "other_names": [],
        "last_modified": None,
        "works": [],
        "employments": [],
    }


def _works(orcid: str, activities: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for group in _get(activities, "works", "group") or []:
        summaries = group.get("work-summary") or []
        if not summaries:
            continue
        work = summaries[0]  # ORCID lists the researcher's preferred version first
        ids = _self_ids(group) | _self_ids(work)
        rows.append(
            {
                "orcid": orcid,
                "put_code": int(work["put-code"]),
                "title": _get(work, "title", "title", "value"),
                "type": work.get("type"),
                "pub_year": _year(_get(work, "publication-date", "year", "value")),
                "journal": _value(work.get("journal-title")),
                "doi": ids.get("doi"),
                "pmid": ids.get("pmid"),
                "pmcid": ids.get("pmc"),
                "source_name": _get(work, "source", "source-name", "value"),
                "versions": len(summaries),
            }
        )
    return rows


def _employments(orcid: str, activities: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for group in _get(activities, "employments", "affiliation-group") or []:
        for item in group.get("summaries") or []:
            job = item.get("employment-summary") or {}
            if "put-code" not in job:
                continue
            org = job.get("organization") or {}
            address = org.get("address") or {}
            disambiguated = org.get("disambiguated-organization") or {}
            rows.append(
                {
                    "orcid": orcid,
                    "put_code": int(job["put-code"]),
                    "organization": org.get("name"),
                    "city": address.get("city"),
                    "region": address.get("region"),
                    "country": address.get("country"),
                    "org_id_source": disambiguated.get("disambiguation-source"),
                    "org_id": disambiguated.get("disambiguated-organization-identifier"),
                    "department": job.get("department-name"),
                    "role": job.get("role-title"),
                    "start_year": _year(_get(job, "start-date", "year", "value")),
                    "end_year": _year(_get(job, "end-date", "year", "value")),
                }
            )
    return rows


def _self_ids(item: dict[str, Any]) -> dict[str, str]:
    """A work's own identifiers (relationship "self"), normalized by type."""
    out: dict[str, str] = {}
    for ext in _get(item, "external-ids", "external-id") or []:
        if ext.get("external-id-relationship") not in (None, "self"):
            continue
        kind = (ext.get("external-id-type") or "").lower()
        value = _get(ext, "external-id-normalized", "value") or ext.get("external-id-value")
        if not value or kind in out:
            continue
        value = str(value).strip()
        if kind == "doi":
            value = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", value, flags=re.I).lower()
        elif kind == "pmid":
            value = re.sub(r"\D", "", value)
        elif kind == "pmc":
            digits = re.sub(r"\D", "", value)
            value = f"PMC{digits}" if digits else ""
        else:
            continue
        if value:
            out[kind] = value
    return out


def _get(data: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _value(item: Any) -> str | None:
    value = item.get("value") if isinstance(item, dict) else None
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _year(value: Any) -> int | None:
    try:
        year = int(str(value))
    except (TypeError, ValueError):
        return None
    return year if 1800 <= year <= 2200 else None


def _millis(value: Any) -> str | None:
    if not isinstance(value, int | float):
        return None
    return datetime.fromtimestamp(value / 1000, UTC).isoformat(timespec="seconds")
