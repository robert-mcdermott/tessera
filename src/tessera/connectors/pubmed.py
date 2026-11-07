"""PubMed via NCBI E-utilities: esearch (with the history server) and efetch XML.

Modes:
  org    affiliation sweep: articles whose author affiliations match the organization profile
  query  any PubMed query (used for person searches)
  ids    specific PMIDs
  linked PMIDs referenced by grant links or study references but not yet in the lake
         (resolved by the ingest layer, then fetched like ``ids``)
"""

from __future__ import annotations

import hashlib
import logging
import os
import pyexpat
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any, ClassVar

from tessera.config import Config
from tessera.connectors.base import Connector, FetchReport, IngestOptions, Payload, Plan, Records
from tessera.connectors.grants import normalize_grant
from tessera.connectors.http import HttpClient, SourceError
from tessera.core.clock import utc_now
from tessera.core.errors import TesseraError

log = logging.getLogger("tessera.connectors.pubmed")

# ElementTree never fetches external entities or DTDs, and expat 2.4.1+ refuses entity
# expansion attacks ("billion laughs"). Refuse to run on an older, vulnerable expat.
if pyexpat.version_info < (2, 4, 1):  # pragma: no cover
    raise ImportError("Tessera needs expat 2.4.1 or newer to parse XML safely.")

RECORD_LIMIT = 10_000  # E-utilities will not page past the first 10,000 records of a search
PMID = re.compile(r"^[1-9][0-9]{0,8}$")


class PubMedConnector(Connector):
    name = "pubmed"
    label = "PubMed"
    datasets = ("pubmed",)
    base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    min_interval = 0.34  # 3 requests/second without an API key
    modes = ("org", "query", "ids", "linked")
    batch_size = 200
    efetch_attempts = 2
    tool: ClassVar[str] = "tessera"

    def __init__(self, http: HttpClient, config: Config) -> None:
        super().__init__(http, config)
        self.api_key = api_key(config)

    @classmethod
    def make_http(cls, config: Config, **kwargs: Any) -> HttpClient:
        interval = 0.1 if api_key(config) else cls.min_interval  # 10/second with a key
        return super().make_http(config, min_interval=interval, **kwargs)

    # -- planning ------------------------------------------------------------------------

    def plan(self, options: IngestOptions, checkpoint: str | None, today: date) -> Plan:
        mode = options.mode
        if mode == "auto":
            mode = "ids" if options.ids else "query" if options.query else "org"
        if mode in {"ids", "linked"}:
            pmids = normalize_pmids(options.ids)
            if mode == "ids" and not pmids:
                raise TesseraError("At least one PMID is required (--ids).")
            return Plan(mode, "", f"{len(pmids)} PMIDs", params={"pmids": pmids}, empty=not pmids)
        if mode not in {"org", "query"}:
            raise TesseraError(f"PubMed has no mode {mode!r}; choose from {', '.join(self.modes)}")
        term = (options.query or "").strip() if mode == "query" else org_term(self.config)
        if not term:
            raise TesseraError("A PubMed query is required (--query).")
        scope = f"{mode}:{hashlib.sha1(term.encode()).hexdigest()[:12]}"  # noqa: S324 - a key
        end = options.until or today
        if options.since:  # records added or changed since a date
            start, date_type = options.since, "mdat"
        elif checkpoint and not options.full:
            start, date_type = date.fromisoformat(checkpoint) - timedelta(days=1), "mdat"
        else:
            start, date_type = _years_before(end, self.config.sync.window_years), "pdat"
        watermark = None if options.max_results else end.isoformat()
        description = f"{term} ({date_type} {start} to {end})"
        params = {
            "term": term,
            "start": start,
            "end": end,
            "date_type": date_type,
            "max_results": options.max_results,
        }
        return Plan(mode, scope, description, new_watermark=watermark, params=params)

    # -- fetching ------------------------------------------------------------------------

    def fetch(self, plan: Plan, report: FetchReport) -> Iterator[Payload]:
        if plan.mode in {"ids", "linked"}:
            pmids = plan.params["pmids"]
            report.total_found = report.expected = len(pmids)
            returned: set[str] = set()
            for start in range(0, len(pmids), self.batch_size):
                chunk = pmids[start : start + self.batch_size]
                params = {"id": ",".join(chunk)}
                for payload in self._efetch(params, {"pmids": [chunk[0], chunk[-1]]}, report):
                    returned.update(returned_pmids(payload.content))
                    yield payload
            missing = [p for p in pmids if p not in returned]
            if missing and not report.failures:
                report.note(
                    f"PubMed returned no record for {len(missing)} PMID(s) "
                    f"(deleted or not yet public): {', '.join(missing[:10])}"
                )
            return
        p = plan.params
        windows, total = self._plan_windows(p["term"], p["start"], p["end"], p["date_type"], report)
        cap = p.get("max_results")
        retrievable = sum(min(w["count"], RECORD_LIMIT) for w in windows)
        expected = min(total, retrievable, cap if cap is not None else total)
        report.total_found, report.expected = total, expected
        if expected < total:
            report.truncated = True
            if cap is None:
                report.note(f"PubMed: only {expected:,} of {total:,} matches can be retrieved.")
        remaining = expected
        for window in windows:
            n = min(window["count"], RECORD_LIMIT, remaining)
            remaining -= n
            for retstart in range(0, n, self.batch_size):
                params = {
                    "WebEnv": window["webenv"],
                    "query_key": window["query_key"],
                    "retstart": retstart,
                    "retmax": min(self.batch_size, n - retstart),
                }
                where = {"window": [str(window["start"]), str(window["end"])], "retstart": retstart}
                yield from self._efetch(params, where, report)

    def _params(self, params: dict[str, Any]) -> dict[str, Any]:
        params = {**params, "tool": self.tool}
        if self.config.sources.contact_email:
            params["email"] = self.config.sources.contact_email
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    def _esearch(self, term: str, start: date, end: date, date_type: str) -> dict[str, Any]:
        response = self.http.post(
            "esearch.fcgi",
            data=self._params(
                {
                    "db": "pubmed",
                    "term": term,
                    "datetype": date_type,
                    "mindate": start.strftime("%Y/%m/%d"),
                    "maxdate": end.strftime("%Y/%m/%d"),
                    "usehistory": "y",
                    "retmax": 0,
                }
            ),
        )
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise SourceError(self.name, f"unreadable esearch response: {exc}") from exc
        if (error := root.find("ERROR")) is not None:
            raise SourceError(self.name, text(error))
        return {
            "count": int(text(root.find("Count")) or 0),
            "webenv": text(root.find("WebEnv")),
            "query_key": text(root.find("QueryKey")),
            "start": start,
            "end": end,
            "translation": opt(root.find("QueryTranslation")),
            "messages": [f"{e.tag}: {text(e)}" for e in root.findall("ErrorList/*")]
            + [
                f"{e.tag}: {text(e)}"
                for e in root.findall("WarningList/*")
                if e.tag != "OutputMessage"
            ],
        }

    def _plan_windows(
        self, term: str, start: date, end: date, date_type: str, report: FetchReport
    ) -> tuple[list[dict[str, Any]], int]:
        first = self._esearch(term, start, end, date_type)
        for message in first["messages"]:
            report.note(f"PubMed query warning: {message} (interpreted as: {first['translation']})")
        if first["count"] <= RECORD_LIMIT:
            return [first], first["count"]
        log.info(
            "%d matches exceed the %d-record limit; splitting by date", first["count"], RECORD_LIMIT
        )
        return self._split(term, date_type, first, report), first["count"]

    def _split(
        self, term: str, date_type: str, window: dict[str, Any], report: FetchReport
    ) -> list[dict[str, Any]]:
        if window["count"] <= RECORD_LIMIT or window["start"] == window["end"]:
            if window["count"] > RECORD_LIMIT:
                report.note(
                    f"PubMed: {window['count']:,} records on {window['start']} exceed the "
                    f"{RECORD_LIMIT:,}-record limit; the rest of that day is unreachable."
                )
            return [window]
        mid = window["start"] + (window["end"] - window["start"]) // 2
        halves = [
            self._esearch(term, window["start"], mid, date_type),
            self._esearch(term, mid + timedelta(days=1), window["end"], date_type),
        ]
        return [w for half in halves for w in self._split(term, date_type, half, report)]

    def _efetch(
        self, params: dict[str, Any], where: dict[str, Any], report: FetchReport
    ) -> Iterator[Payload]:
        body = self._params({"db": "pubmed", "retmode": "xml", **params})
        last: Exception | None = None
        for attempt in range(1, self.efetch_attempts + 1):
            try:
                response = self.http.post("efetch.fcgi", data=body)
                ET.fromstring(response.content)  # validate before saving
            except (SourceError, ET.ParseError) as exc:
                # The history server intermittently answers 400 for a valid WebEnv.
                last = exc
                if attempt < self.efetch_attempts:
                    self.http.sleep(2.0)
                continue
            report.payloads += 1
            yield Payload("efetch", response.content, "xml", utc_now(), request=where)
            return
        report.fail(where, last or "unknown error")

    # -- parsing -------------------------------------------------------------------------

    @classmethod
    def parse(cls, payload: Payload) -> Records:
        root = ET.fromstring(payload.content)
        records = []
        for elem in root:
            parser = PARSERS.get(elem.tag)
            if parser is None:
                continue
            record = parser(elem)
            if record is not None:
                records.append(record)
        return {"pubmed": records}


# --- helpers ------------------------------------------------------------------------------


def api_key(config: Config) -> str | None:
    name = config.sources.pubmed.api_key_env
    return (os.environ.get(name) or None) if name else None


def returned_pmids(content: bytes) -> set[str]:
    root = ET.fromstring(content)
    found = set()
    for elem in root:
        pmid = elem.findtext("MedlineCitation/PMID") or elem.findtext("BookDocument/PMID")
        if pmid:
            found.add(pmid.strip())
    return found


def normalize_pmids(values: list[str]) -> list[str]:
    pmids = [str(v).strip() for v in values if str(v).strip()]
    bad = [p for p in pmids if not PMID.match(p)]
    if bad:
        raise TesseraError(f"Invalid PMID(s): {', '.join(bad[:5])}")
    return list(dict.fromkeys(pmids))


def _phrase(value: str) -> str:
    return '"' + value.replace('"', " ").strip() + '"'


def org_term(config: Config) -> str:
    """An affiliation query for the organization profile."""
    org = config.organization
    names = list(dict.fromkeys([*org.aliases, *org.affiliation_patterns]))
    if not names:
        raise TesseraError(
            "A PubMed sweep needs organization.aliases or organization.affiliation_patterns."
        )
    return " OR ".join(f"{_phrase(n)}[ad]" for n in names)


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February
        return day.replace(year=day.year - years, day=28)


# --- XML parsing (records shaped for the lake; see tessera.lake.schemas.PUBMED) ----------

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
)}  # fmt: skip
ORCID_RE = re.compile(r"(\d{4}-?\d{4}-?\d{4}-?\d{3}[\dX])", re.IGNORECASE)
RETRACTED_TYPE = "Retracted Publication"


def text(elem: ET.Element | None) -> str:
    """All text inside an element, including inline markup such as <i> or <sup>."""
    return " ".join("".join(elem.itertext()).split()) if elem is not None else ""


def opt(elem: ET.Element | None) -> str | None:
    return text(elem) or None


def _int(value: str) -> int | None:
    return int(value) if value.isdigit() else None


def _month(value: str) -> int | None:
    return MONTHS.get(value[:3].lower()) or _int(value)


def parse_date(elem: ET.Element | None) -> tuple[date | None, int | None, str | None]:
    """A PubDate-like element as (first day of the period, year, precision).

    MedlineDate values keep only what is certain: "1991 Dec 23-30" gives December 1991, while
    ranges such as "2023 Jan-Feb" or "1998-1999" give just the (first) year.
    """
    if elem is None:
        return None, None, None
    year = _int(text(elem.find("Year")))
    month = _month(text(elem.find("Month")))
    day = _int(text(elem.find("Day")))
    medline = text(elem.find("MedlineDate"))
    if year is None and medline:
        match = re.match(r"(\d{4})(?:\s+([A-Za-z]{3})[a-z]*(?![-/]))?", medline)
        if match:
            year = int(match.group(1))
            month = _month(match.group(2)) if match.group(2) else None
            day = None
    if year is None:
        return None, None, None
    if month is None or not 1 <= month <= 12:
        return date(year, 1, 1), year, "year"
    if day is None:
        return date(year, month, 1), year, "month"
    try:
        return date(year, month, day), year, "day"
    except ValueError:
        return date(year, month, 1), year, "month"


def normalize_orcid(value: str) -> str | None:
    match = ORCID_RE.search(value.replace(" ", ""))
    if not match:
        return None
    digits = match.group(1).replace("-", "").upper()
    return "-".join(digits[i : i + 4] for i in range(0, 16, 4))


def _texts(parent: ET.Element | None, path: str) -> list[str]:
    return [t for e in parent.findall(path) if (t := text(e))] if parent is not None else []


def _authors(author_list: ET.Element | None) -> list[dict[str, Any]]:
    if author_list is None:
        return []
    elements = author_list.findall("Author")
    authors = []
    for position, author in enumerate(elements, 1):
        family, given = opt(author.find("LastName")), opt(author.find("ForeName"))
        collective = opt(author.find("CollectiveName"))
        if not (family or collective):
            continue
        orcid = next(
            (
                o
                for i in author.findall("Identifier")
                if i.get("Source") == "ORCID" and (o := normalize_orcid(text(i)))
            ),
            None,
        )
        authors.append(
            {
                "position": position,
                "family": family,
                "given": given,
                "initials": opt(author.find("Initials")),
                "suffix": opt(author.find("Suffix")),
                "collective_name": collective,
                "orcid": orcid,
                "affiliations": _texts(author, "AffiliationInfo/Affiliation"),
                "is_first": position == 1,
                "is_last": position == len(elements),
                "author_count": len(elements),
            }
        )
    return authors


def _abstract(container: ET.Element | None) -> str | None:
    parts = []
    if container is not None:
        for elem in container.findall("Abstract/AbstractText"):
            if body := text(elem):
                label = elem.get("Label")
                parts.append(f"{label}: {body}" if label else body)
    return " ".join(parts) or None


def _article_ids(id_list: ET.Element | None) -> dict[str, str]:
    ids: dict[str, str] = {}
    if id_list is not None:
        for elem in id_list.findall("ArticleId"):
            if (id_type := elem.get("IdType")) and (value := text(elem)):
                ids.setdefault(id_type, value)
    return ids


def _mesh(citation: ET.Element) -> list[dict[str, Any]]:
    headings: dict[str, dict[str, Any]] = {}
    for heading in citation.findall("MeshHeadingList/MeshHeading"):
        descriptor = heading.find("DescriptorName")
        if descriptor is None or not descriptor.get("UI") or not text(descriptor):
            continue
        ui = descriptor.get("UI") or ""
        entry = headings.setdefault(
            ui,
            {
                "descriptor_ui": ui,
                "descriptor": text(descriptor),
                "major_topic": False,
                "qualifiers": [],
            },
        )
        qualifiers = heading.findall("QualifierName")
        entry["major_topic"] = (
            entry["major_topic"]
            or descriptor.get("MajorTopicYN") == "Y"
            or any(q.get("MajorTopicYN") == "Y" for q in qualifiers)
        )
        entry["qualifiers"] += [t for q in qualifiers if (t := text(q)) not in entry["qualifiers"]]
    return list(headings.values())


def _grants(art: ET.Element) -> list[dict[str, Any]]:
    grants = []
    for seq, grant in enumerate(art.findall("GrantList/Grant"), 1):
        raw, agency = opt(grant.find("GrantID")), opt(grant.find("Agency"))
        core, ic_serial = normalize_grant(raw, agency)
        grants.append(
            {
                "seq": seq,
                "grant_id_raw": raw,
                "core_project_num": core,
                "ic_serial": ic_serial,
                "acronym": opt(grant.find("Acronym")),
                "agency": agency,
                "country": opt(grant.find("Country")),
            }
        )
    return grants


def parse_journal_article(elem: ET.Element) -> dict[str, Any] | None:
    citation = elem.find("MedlineCitation")
    art = citation.find("Article") if citation is not None else None
    pmid = text(citation.find("PMID")) if citation is not None else ""
    if citation is None or art is None or not PMID.match(pmid):
        log.warning("skipping a malformed PubmedArticle record")
        return None
    issue = art.find("Journal/JournalIssue")
    pub_date, pub_year, precision = parse_date(art.find("Journal/JournalIssue/PubDate"))
    epub = next((d for d in art.findall("ArticleDate") if d.get("DateType") == "Electronic"), None)
    ids = _article_ids(elem.find("PubmedData/ArticleIdList"))
    comments = [
        {
            "ref_type": c.get("RefType"),
            "pmid": opt(c.find("PMID")),
            "note": opt(c.find("RefSource")),
        }
        for c in citation.findall("CommentsCorrectionsList/CommentsCorrections")
    ]
    publication_types = _texts(art, "PublicationTypeList/PublicationType")
    eloc_doi = next(
        (text(e) for e in art.findall("ELocationID") if e.get("EIdType") == "doi" and text(e)), None
    )
    return {
        "pmid": pmid,
        "record_type": "journal_article",
        "doi": ids.get("doi") or eloc_doi,
        "pmcid": ids.get("pmc"),
        "title": opt(art.find("ArticleTitle")) or opt(art.find("VernacularTitle")),
        "abstract": _abstract(art),
        "journal": opt(art.find("Journal/Title")),
        "journal_abbrev": opt(art.find("Journal/ISOAbbreviation"))
        or opt(citation.find("MedlineJournalInfo/MedlineTA")),
        "issn": opt(art.find("Journal/ISSN")),
        "volume": opt(issue.find("Volume")) if issue is not None else None,
        "issue": opt(issue.find("Issue")) if issue is not None else None,
        "pages": opt(art.find("Pagination/MedlinePgn")),
        "pub_date": pub_date,
        "pub_year": pub_year,
        "date_precision": precision,
        "epub_date": parse_date(epub)[0] if epub is not None else None,
        "publication_types": publication_types,
        "publication_status": opt(elem.find("PubmedData/PublicationStatus")),
        "languages": _texts(art, "Language"),
        "keywords": _texts(citation, "KeywordList/Keyword"),
        "is_retracted": RETRACTED_TYPE in publication_types
        or any(c["ref_type"] == "RetractionIn" for c in comments),
        "comments_corrections": comments,
        "authors": _authors(art.find("AuthorList")),
        "mesh": _mesh(citation),
        "grants": _grants(art),
    }


def parse_book_article(elem: ET.Element) -> dict[str, Any] | None:
    doc = elem.find("BookDocument")
    pmid = text(doc.find("PMID")) if doc is not None else ""
    if doc is None or not PMID.match(pmid):
        log.warning("skipping a malformed PubmedBookArticle record")
        return None
    book = doc.find("Book")
    author_list = next(
        (a for a in doc.findall("AuthorList") if a.get("Type", "authors") == "authors"), None
    )
    book_title = opt(book.find("BookTitle")) if book is not None else None
    pub_date, pub_year, precision = parse_date(book.find("PubDate") if book is not None else None)
    ids = {
        **_article_ids(doc.find("ArticleIdList")),
        **_article_ids(elem.find("PubmedBookData/ArticleIdList")),
    }
    return {
        "pmid": pmid,
        "record_type": "book",
        "doi": ids.get("doi"),
        "pmcid": ids.get("pmc"),
        "title": opt(doc.find("ArticleTitle")) or book_title,
        "abstract": _abstract(doc),
        "journal": book_title,
        "journal_abbrev": None,
        "issn": None,
        "volume": opt(book.find("Volume")) if book is not None else None,
        "issue": None,
        "pages": None,
        "pub_date": pub_date,
        "pub_year": pub_year,
        "date_precision": precision,
        "epub_date": parse_date(doc.find("ContributionDate"))[0],
        "publication_types": _texts(doc, "PublicationType"),
        "publication_status": opt(elem.find("PubmedBookData/PublicationStatus")),
        "languages": _texts(doc, "Language"),
        "keywords": _texts(doc, "KeywordList/Keyword"),
        "is_retracted": False,
        "comments_corrections": [],
        "authors": _authors(author_list),
        "mesh": [],
        "grants": [],
    }


PARSERS = {"PubmedArticle": parse_journal_article, "PubmedBookArticle": parse_book_article}
