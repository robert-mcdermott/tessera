"""Citation metrics from NIH iCite for publications already in the lake."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

from tessera.connectors.base import Connector, FetchReport, IngestOptions, Payload, Plan, Records
from tessera.connectors.http import SourceError
from tessera.connectors.pubmed import normalize_pmids
from tessera.core.clock import utc_now

FIELDS = (
    "pmid",
    "year",
    "citation_count",
    "relative_citation_ratio",
    "nih_percentile",
    "field_citation_rate",
    "expected_citations_per_year",
    "citations_per_year",
    "apt",
    "is_clinical",
    "is_research_article",
)


class ICiteConnector(Connector):
    name = "icite"
    label = "iCite"
    datasets = ("icite",)
    base_url = "https://icite.od.nih.gov/api/"
    min_interval = 0.25
    modes = ("ids", "refresh")
    batch_size = 200

    def plan(self, options: IngestOptions, checkpoint: str | None, today: date) -> Plan:
        # In "auto" mode the ingest layer supplies the PMIDs that need metrics.
        pmids = normalize_pmids(options.ids)
        mode = "ids" if options.mode == "ids" else "refresh"
        return Plan(
            mode, "", f"metrics for {len(pmids)} PMIDs", params={"pmids": pmids}, empty=not pmids
        )

    def fetch(self, plan: Plan, report: FetchReport) -> Iterator[Payload]:
        pmids = plan.params["pmids"]
        report.total_found = report.expected = len(pmids)
        for start in range(0, len(pmids), self.batch_size):
            chunk = pmids[start : start + self.batch_size]
            where = {"pmids": [chunk[0], chunk[-1]], "count": len(chunk)}
            try:
                response = self.http.get(
                    "pubs",
                    params={"pmids": ",".join(chunk), "format": "json", "fl": ",".join(FIELDS)},
                )
                response.json()
            except (SourceError, ValueError) as exc:
                report.fail(where, exc)
                continue
            report.payloads += 1
            yield Payload("pubs", response.content, "json", utc_now(), request=where)

    @classmethod
    def parse(cls, payload: Payload) -> Records:
        records = []
        for item in payload.json().get("data") or []:
            pmid = item.get("pmid") or item.get("_id")
            if pmid in (None, ""):
                continue
            records.append(
                {
                    "pmid": str(pmid),
                    "year": _int(item.get("year")),
                    "citation_count": _int(item.get("citation_count")),
                    "relative_citation_ratio": _float(item.get("relative_citation_ratio")),
                    "nih_percentile": _float(item.get("nih_percentile")),
                    "field_citation_rate": _float(item.get("field_citation_rate")),
                    "expected_citations_per_year": _float(item.get("expected_citations_per_year")),
                    "citations_per_year": _float(item.get("citations_per_year")),
                    "apt": _float(item.get("apt")),
                    "is_clinical": _bool(item.get("is_clinical")),
                    "is_research_article": _bool(item.get("is_research_article")),
                }
            )
        return {"icite": records}


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool) or value is None:
        return value
    return {"yes": True, "true": True, "y": True, "no": False, "false": False, "n": False}.get(
        str(value).strip().lower()
    )
