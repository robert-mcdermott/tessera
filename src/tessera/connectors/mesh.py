"""MeSH, the National Library of Medicine's vocabulary of headings, from its annual descriptor file.

NLM publishes the next year's MeSH in November or December as one XML file
(``desc{year}.gz``, about 17 MB compressed, 300 MB as XML). Tessera reads it once a year:
each heading's identifier and name, its tree numbers (where it sits in the hierarchy), its
entry terms (the synonyms people write, such as "Cancer" for "Neoplasms"), and its scope
note. NLM is credited as the source, with the MeSH year, wherever the data is used.

The download is kept in the raw zone as received, and parsed as a stream, so the 300 MB of
XML never sits in memory.
"""

from __future__ import annotations

import gzip
import io
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import date
from typing import Any

from tessera.config import Config
from tessera.connectors.base import Connector, FetchReport, IngestOptions, Payload, Plan, Records
from tessera.connectors.http import HttpClient, SourceError
from tessera.core.clock import utc_now

MAX_TERM = 100  # longer entry terms are systematic chemical names, never typed by anyone


class MeshConnector(Connector):
    name = "mesh"
    label = "MeSH"
    datasets = ("mesh",)
    base_url = "https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/"
    min_interval = 1.0
    modes = ("annual",)

    @classmethod
    def make_http(cls, config: Config, **kwargs: Any) -> HttpClient:
        # One 17 MB file: allow it time on a slow connection. NLM labels the .gz file as
        # gzip-encoded when asked for gzip, and the client would unpack it to 300 MB of XML;
        # asking for the file as it is keeps it compressed.
        headers = {"Accept-Encoding": "identity", **kwargs.pop("headers", {})}
        return super().make_http(
            config, timeout=kwargs.pop("timeout", 300.0), headers=headers, **kwargs
        )

    def plan(self, options: IngestOptions, checkpoint: str | None, today: date) -> Plan:
        # The next year's MeSH appears in November or December; until then, this year's.
        years = [today.year + 1, today.year] if today.month >= 11 else [today.year]
        if options.ids:
            years = [int(y) for y in options.ids]
        have = int(checkpoint) if checkpoint and checkpoint.isdigit() else 0
        wanted = [y for y in years if y > have]
        return Plan(
            "annual",
            "descriptors",
            f"MeSH descriptors ({' or '.join(map(str, wanted))})"
            if wanted
            else f"MeSH {have} is current",
            params={"years": wanted},
            empty=not wanted,
        )

    def fetch(self, plan: Plan, report: FetchReport) -> Iterator[Payload]:
        report.expected = 1
        for year in plan.params["years"]:
            where = {"year": year}
            try:
                response = self.http.get(f"desc{year}.gz")
            except SourceError as exc:
                if exc.status == 404:  # not published yet: try the year before
                    continue
                report.fail(where, exc)
                return
            if not _is_descriptor_file(response.content):
                # NLM answers a file it does not have with an HTML page and a 200 status.
                continue
            report.total_found = 1
            report.payloads += 1
            plan.new_watermark = str(year)
            yield Payload(
                "descriptors",
                response.content,
                "gzip",
                utc_now(),
                request=where,
                source_version=f"MeSH {year}",
            )
            return
        report.note("No newer MeSH descriptor file has been published yet.")

    @classmethod
    def parse(cls, payload: Payload) -> Records:
        year = int(payload.request.get("year") or str(payload.source_version or "0").split()[-1])
        return {"mesh": list(descriptors(payload.content, year))}


def _is_descriptor_file(content: bytes) -> bool:
    """Gzipped XML, or XML a proxy has already unpacked; never an HTML error page."""
    if content[:2] == b"\x1f\x8b":
        return True
    head = content[:600].lstrip()
    return head.startswith(b"<?xml") and b"DescriptorRecordSet" in head


def _text(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    return " ".join(element.text.split()) or None


def descriptors(content: bytes, year: int) -> Iterator[dict[str, Any]]:
    """Heading records from a gzipped (or plain) MeSH descriptor XML file."""
    stream: io.BufferedIOBase = io.BytesIO(content)
    if content[:2] == b"\x1f\x8b":
        stream = gzip.GzipFile(fileobj=stream)  # type: ignore[assignment]
    for _, element in ET.iterparse(stream, events=("end",)):  # noqa: S314 (NLM's own file)
        if element.tag != "DescriptorRecord":
            continue
        ui = _text(element.find("DescriptorUI"))
        name = _text(element.find("DescriptorName/String"))
        if ui and name:
            terms: dict[str, str] = {}
            scope = None
            for concept in element.iterfind("ConceptList/Concept"):
                if concept.get("PreferredConceptYN") == "Y":
                    scope = _text(concept.find("ScopeNote"))
                for term in concept.iterfind("TermList/Term"):
                    if term.get("IsPermutedTermYN") == "Y":
                        continue  # "Neoplasm, Benign" says nothing "Benign Neoplasm" does not
                    text = _text(term.find("String"))
                    if text and len(text) <= MAX_TERM and text.lower() != name.lower():
                        terms.setdefault(text.lower(), text)
            updated = element.find("LastUpdated")
            yield {
                "descriptor_ui": ui,
                "name": name,
                "descriptor_class": int(element.get("DescriptorClass") or 0) or None,
                "tree_numbers": [
                    t.text.strip() for t in element.iterfind("TreeNumberList/TreeNumber") if t.text
                ],
                "entry_terms": sorted(terms.values(), key=str.lower),
                "scope_note": scope[:2000] if scope else None,
                "mesh_year": year,
                "last_updated": _date(updated),
            }
        element.clear()  # keep memory flat while streaming


def _date(element: ET.Element | None) -> date | None:
    if element is None:
        return None
    try:
        return date(
            int(element.findtext("Year") or 0),
            int(element.findtext("Month") or 1),
            int(element.findtext("Day") or 1),
        )
    except ValueError:
        return None


__all__ = ["MeshConnector", "descriptors"]
