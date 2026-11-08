from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime

import httpx
import pytest

from tessera.config import Config, load_config
from tessera.connectors.base import FetchReport, IngestOptions, Payload
from tessera.connectors.ctgov import CtgovConnector, org_expression, parse_study, scrub_contacts
from tessera.connectors.grants import normalize_grant
from tessera.connectors.icite import ICiteConnector
from tessera.connectors.orcid import OrcidConnector, orcid_checksum_ok, valid_orcids
from tessera.connectors.pubmed import PubMedConnector, org_term, parse_date
from tessera.connectors.reporter import (
    LINK_LIMIT,
    SEARCH_LIMIT,
    ReporterConnector,
    fiscal_year,
    parse_criteria,
)
from tessera.core.errors import TesseraError

from .fakes import (
    EFETCH_XML,
    ctgov_api,
    ctgov_study,
    esearch_xml,
    form,
    no_sleep,
    orcid_api,
    orcid_record,
    pubmed_api,
    reporter_api,
    reporter_project,
)

TODAY = date(2026, 11, 7)


@pytest.fixture
def config(config_file) -> Config:  # type: ignore[no-untyped-def]
    return load_config(config_file, env={})


def make(cls, config: Config, api):  # type: ignore[no-untyped-def]
    return cls(cls.make_http(config, transport=api.transport(), sleep=no_sleep), config)


def collect(connector, plan) -> tuple[list[Payload], FetchReport]:  # type: ignore[no-untyped-def]
    report = FetchReport()
    return list(connector.fetch(plan, report)), report


# --- grants ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "agency", "expected"),
    [
        ("R01 CA123456", None, ("R01CA123456", "CA123456")),
        ("5R01CA123456-05", None, ("R01CA123456", "CA123456")),
        ("R01-CA-123456", None, ("R01CA123456", "CA123456")),
        ("U54 GM104940-01A1", None, ("U54GM104940", "GM104940")),
        ("UL1 TR002319", None, ("UL1TR002319", "TR002319")),
        ("ZIA BC010001", None, ("ZIABC010001", "BC010001")),
        ("R01CA12345", None, ("R01CA012345", "CA012345")),
        ("CA15704", "NCI NIH HHS", (None, "CA015704")),
        ("CA15704", "Example Charitable Trust", (None, None)),
        ("MOP-123456", None, (None, None)),
        ("75N91019D00024", "NCI NIH HHS", (None, None)),  # a contract, not a grant
        ("", None, (None, None)),
        (None, None, (None, None)),
    ],
)
def test_normalize_grant(raw, agency, expected) -> None:  # type: ignore[no-untyped-def]
    assert normalize_grant(raw, agency) == expected


# --- PubMed parsing --------------------------------------------------------------------------


def test_pubmed_parse_fixture() -> None:
    payload = Payload("efetch", EFETCH_XML, "xml", datetime.now(UTC))
    records = {r["pmid"]: r for r in PubMedConnector.parse(payload)["pubmed"]}
    assert set(records) == {"31000001", "31000002", "31000003", "31000004"}  # malformed skipped

    a = records["31000001"]
    assert a["title"] == "Mapping tiles in research mosaics."
    assert a["abstract"] == "BACKGROUND: Tiles matter. RESULTS: They connect."
    assert a["doi"] == "10.5555/jes.2024.001" and a["pmcid"] == "PMC9000001"
    assert (a["pub_date"], a["pub_year"], a["date_precision"]) == (date(2024, 3, 15), 2024, "day")
    assert a["epub_date"] == date(2024, 2, 1)
    assert a["journal_abbrev"] == "J Ex Stud" and a["pages"] == "101-110"
    assert a["is_retracted"] is False

    first, second, third = a["authors"]
    assert first["orcid"] == "0000-0002-1825-009X"
    assert first["affiliations"][1].endswith("ada.okafor@example.edu.")
    assert (first["is_first"], first["is_last"], first["author_count"]) == (True, False, 3)
    assert second["suffix"] == "Jr"
    assert third["collective_name"] == "Mosaic Consortium Investigators" and third["is_last"]

    mesh = {m["descriptor_ui"]: m for m in a["mesh"]}
    assert set(mesh) == {"D006801", "D012107"}  # duplicate merged, missing UI dropped
    assert mesh["D012107"]["major_topic"] is True  # a major qualifier makes the topic major
    assert mesh["D012107"]["qualifiers"] == ["methods", "trends"]

    grants = a["grants"]
    assert [(g["core_project_num"], g["ic_serial"]) for g in grants] == [
        ("R01CA123456", "CA123456"),
        (None, "CA015704"),
        (None, None),
    ]

    b = records["31000002"]
    # A month range ("2019 Nov-Dec") keeps only the year rather than claim false precision.
    assert (b["pub_date"], b["date_precision"]) == (date(2019, 1, 1), "year")
    assert b["is_retracted"] is True
    assert records["31000003"]["date_precision"] == "year"
    book = records["31000004"]
    assert book["record_type"] == "book" and book["journal"] == "The Example Handbook"
    assert book["authors"][0]["family"] == "Silva"


@pytest.mark.parametrize(
    ("xml", "expected"),
    [
        ("<d><Year>2020</Year><Month>Feb</Month><Day>30</Day></d>", (date(2020, 2, 1), "month")),
        ("<d><Year>2020</Year><Month>13</Month></d>", (date(2020, 1, 1), "year")),
        ("<d><MedlineDate>1998-1999</MedlineDate></d>", (date(1998, 1, 1), "year")),
        ("<d><MedlineDate>Spring</MedlineDate></d>", (None, None)),
    ],
)
def test_pubmed_odd_dates(xml: str, expected) -> None:  # type: ignore[no-untyped-def]
    import xml.etree.ElementTree as ET

    parsed = parse_date(ET.fromstring(xml))
    assert (parsed[0], parsed[2]) == expected


# --- PubMed planning and fetching -------------------------------------------------------------


def test_pubmed_org_term(config: Config) -> None:
    term = org_term(config)
    assert '"Example University"[ad]' in term
    assert '"example univ*"[ad]' in term
    assert term.count(" OR ") == 4


def test_pubmed_plan_backfill_then_incremental(config: Config) -> None:
    connector = PubMedConnector(None, config)  # type: ignore[arg-type]
    first = connector.plan(IngestOptions(), None, TODAY)
    assert first.mode == "org" and first.params["date_type"] == "pdat"
    assert first.params["start"] == date(2021, 11, 7)
    assert first.new_watermark == "2026-11-07"
    later = connector.plan(IngestOptions(), "2026-11-01", TODAY)
    assert later.scope == first.scope
    assert later.params["date_type"] == "mdat" and later.params["start"] == date(2026, 10, 31)
    full = connector.plan(IngestOptions(full=True), "2026-11-01", TODAY)
    assert full.params["date_type"] == "pdat"
    capped = connector.plan(IngestOptions(max_results=5), None, TODAY)
    assert capped.new_watermark is None  # quick checks never move the checkpoint


def test_pubmed_plan_ids(config: Config) -> None:
    connector = PubMedConnector(None, config)  # type: ignore[arg-type]
    plan = connector.plan(IngestOptions(ids=["2", " 1", "2"]), None, TODAY)
    assert plan.mode == "ids" and plan.params["pmids"] == ["2", "1"]
    with pytest.raises(TesseraError, match="Invalid PMID"):
        connector.plan(IngestOptions(ids=["12a"]), None, TODAY)
    with pytest.raises(TesseraError, match="At least one PMID"):
        connector.plan(IngestOptions(mode="ids"), None, TODAY)
    assert connector.plan(IngestOptions(mode="linked"), None, TODAY).empty


def test_pubmed_search_fetch(config: Config) -> None:
    api = pubmed_api(count=4)
    connector = make(PubMedConnector, config, api)
    plan = connector.plan(IngestOptions(), None, TODAY)
    payloads, report = collect(connector, plan)
    assert len(payloads) == 1 and report.complete
    assert report.total_found == report.expected == 4
    search = form(api.calls("esearch.fcgi")[0])
    assert search["datetype"] == "pdat" and search["usehistory"] == "y"
    assert search["tool"] == "tessera" and search["email"] == "research-it@example.edu"
    fetch = form(api.calls("efetch.fcgi")[0])
    assert (fetch["WebEnv"], fetch["retstart"], fetch["retmax"]) == ("WE1", "0", "4")


def test_pubmed_splits_large_searches_by_date(config: Config) -> None:
    def esearch(request: httpx.Request) -> httpx.Response:
        params = form(request)
        start = date(*map(int, params["mindate"].split("/")))
        end = date(*map(int, params["maxdate"].split("/")))
        days = (end - start).days + 1
        return httpx.Response(200, content=esearch_xml(days * 10, webenv=params["mindate"]))

    api = pubmed_api()
    api.route("esearch.fcgi", esearch)
    connector = make(PubMedConnector, config, api)
    plan = connector.plan(
        IngestOptions(since=date(2025, 1, 1), until=date(2026, 12, 31)), None, TODAY
    )
    _, report = collect(connector, plan)
    assert report.total_found == 7300
    windows = {form(r)["WebEnv"] for r in api.calls("efetch.fcgi")}
    assert len(windows) == 1  # 7,300 fits in one search
    plan = connector.plan(
        IngestOptions(since=date(2020, 1, 1), until=date(2026, 12, 31)), None, TODAY
    )
    api.requests.clear()
    _payloads, report = collect(connector, plan)
    windows = {form(r)["WebEnv"] for r in api.calls("efetch.fcgi")}
    assert report.total_found == 25570 and len(windows) == 4  # split into 4 windows under 10k
    assert report.complete


def test_pubmed_efetch_retries_history_server_400(config: Config) -> None:
    responses = iter(
        [httpx.Response(400, text="bad WebEnv"), httpx.Response(200, content=EFETCH_XML)]
    )
    api = pubmed_api(efetch=lambda r: next(responses))
    connector = make(PubMedConnector, config, api)
    payloads, report = collect(connector, connector.plan(IngestOptions(), None, TODAY))
    assert len(payloads) == 1 and report.complete


def test_pubmed_failed_batch_makes_report_incomplete(config: Config) -> None:
    api = pubmed_api(efetch=lambda r: httpx.Response(400, text="nope"))
    connector = make(PubMedConnector, config, api)
    payloads, report = collect(connector, connector.plan(IngestOptions(), None, TODAY))
    assert payloads == [] and not report.complete and len(report.failures) == 1


def test_pubmed_query_warnings_are_reported(config: Config) -> None:
    api = pubmed_api()
    api.route(
        "esearch.fcgi",
        lambda r: httpx.Response(200, content=esearch_xml(4, warning="exu medical center")),
    )
    connector = make(PubMedConnector, config, api)
    _, report = collect(connector, connector.plan(IngestOptions(), None, TODAY))
    assert any("exu medical center" in n for n in report.notes)


def test_pubmed_ids_report_missing(config: Config) -> None:
    connector = make(PubMedConnector, config, pubmed_api())
    plan = connector.plan(IngestOptions(ids=["31000001", "39999999"]), None, TODAY)
    _, report = collect(connector, plan)
    assert any("39999999" in n for n in report.notes)


def test_pubmed_api_key_speeds_up_requests(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    assert PubMedConnector.make_http(config).limiter.min_interval == 0.34
    monkeypatch.setenv("NCBI_API_KEY", "test-key")
    http = PubMedConnector.make_http(config)
    assert http.limiter.min_interval == 0.1
    api = pubmed_api()
    connector = make(PubMedConnector, config, api)
    collect(connector, connector.plan(IngestOptions(), None, TODAY))
    assert form(api.calls("esearch.fcgi")[0])["api_key"] == "test-key"


# --- RePORTER --------------------------------------------------------------------------------


def test_fiscal_year() -> None:
    assert fiscal_year(date(2026, 9, 30)) == 2026
    assert fiscal_year(date(2026, 10, 1)) == 2027


def test_reporter_parse_project() -> None:
    payload = Payload(
        "projects",
        json.dumps({"results": [reporter_project(11, "R01CA123456")]}).encode(),
        "json",
        datetime.now(UTC),
    )
    [record] = ReporterConnector.parse(payload)["reporter"]
    assert record["appl_id"] == 11 and record["core_project_num"] == "R01CA123456"
    assert record["award_amount"] == 500000 and isinstance(record["award_amount"], int)
    assert record["project_start"] == date(2021, 7, 1)
    assert record["org_name"] == "EXAMPLE UNIVERSITY"
    assert record["terms"] == ["Mosaics", "Tiles", "Research"]
    assert record["spending_categories"] == ["Cancer", "Genetics"]
    assert record["abstract"] == "An abstract."
    [pi] = record["investigators"]  # the PI without a profile ID is dropped
    assert (pi["profile_id"], pi["full_name"], pi["is_contact_pi"]) == (9001, "Ada B Okafor", True)


def test_reporter_parse_links_dedupes() -> None:
    rows = [
        {"pmid": 1, "coreproject": "r01ca1", "applid": 5},
        {"pmid": 1, "coreproject": "R01CA1"},
        {"pmid": None, "coreproject": "X"},
    ]
    payload = Payload("links", json.dumps({"results": rows}).encode(), "json", datetime.now(UTC))
    assert ReporterConnector.parse(payload)["reporter_links"] == [
        {"pmid": "1", "core_project_num": "R01CA1", "appl_id": None}
    ]


def test_reporter_plan(config: Config) -> None:
    connector = ReporterConnector(None, config)  # type: ignore[arg-type]
    first = connector.plan(IngestOptions(), None, TODAY)  # FY2027 began 1 Oct 2026
    assert first.params["criteria"] == {
        "org_names": ["EXAMPLE UNIVERSITY"],
        "fiscal_years": list(range(2022, 2029)),
    }
    assert first.new_watermark == "2027"
    later = connector.plan(IngestOptions(), "2027", TODAY)
    assert later.params["criteria"]["fiscal_years"] == [2026, 2027, 2028]
    with pytest.raises(TesseraError, match="Unknown RePORTER criteria"):
        parse_criteria('{"org_name": ["typo"]}')
    with pytest.raises(TesseraError, match="JSON"):
        parse_criteria("not json")


def test_reporter_fetch_projects_and_links(config: Config) -> None:
    projects = [reporter_project(i, f"R01CA{i:06d}") for i in range(1, 8)]
    links = {"R01CA000001": ["101", "102"], "R01CA000002": ["103"]}
    api = reporter_api(projects, links)
    connector = make(ReporterConnector, config, api)
    plan = connector.plan(IngestOptions(), None, TODAY)
    payloads, report = collect(connector, plan)
    kinds = [p.kind for p in payloads]
    assert kinds == ["projects", "links"]
    assert report.total_found == 7 and report.complete
    assert report.data_version == "2025-11-01T00:00:00"
    linked = ReporterConnector.parse(payloads[-1])["reporter_links"]
    assert {(r["pmid"], r["core_project_num"]) for r in linked} == {
        ("101", "R01CA000001"),
        ("102", "R01CA000001"),
        ("103", "R01CA000002"),
    }


def test_reporter_paging(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tessera.connectors.reporter.PAGE_SIZE", 3)
    projects = [reporter_project(i, f"R01CA{i:06d}") for i in range(1, 8)]
    connector = make(ReporterConnector, config, reporter_api(projects, {}))
    payloads, _report = collect(connector, connector.plan(IngestOptions(), None, TODAY))
    pages = [p for p in payloads if p.kind == "projects"]
    assert [len(p.json()["results"]) for p in pages] == [3, 3, 1]


def test_reporter_splits_over_search_limit(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tessera.connectors.reporter.SEARCH_LIMIT", 4)
    projects = [reporter_project(i, f"R01CA{i:06d}", fy=2024 + i % 3) for i in range(1, 10)]
    connector = make(ReporterConnector, config, reporter_api(projects, {}))
    payloads, report = collect(connector, connector.plan(IngestOptions(), None, TODAY))
    seen = {p["appl_id"] for pl in payloads if pl.kind == "projects" for p in pl.json()["results"]}
    assert seen == set(range(1, 10)) and report.complete
    assert SEARCH_LIMIT == 15_000  # the module constant is untouched outside this test


def test_reporter_link_failures_are_narrowed_to_one_project(config: Config) -> None:
    projects = [reporter_project(i, f"R01CA{i:06d}") for i in range(1, 5)]
    links = {f"R01CA{i:06d}": [str(100 + i)] for i in range(1, 5)}
    api = reporter_api(projects, links, fail_cores={"R01CA000003"})
    connector = make(ReporterConnector, config, api)
    payloads, report = collect(connector, connector.plan(IngestOptions(), None, TODAY))
    linked = {
        r["core_project_num"]
        for p in payloads
        if p.kind == "links"
        for r in ReporterConnector.parse(p)["reporter_links"]
    }
    assert linked == {"R01CA000001", "R01CA000002", "R01CA000004"}
    assert [f["core_project_num"] for f in report.failures] == ["R01CA000003"]
    assert not report.complete


def test_reporter_halves_link_batches_over_limit(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tessera.connectors.reporter.LINK_LIMIT", 3)
    projects = [reporter_project(i, f"R01CA{i:06d}") for i in range(1, 5)]
    links = {f"R01CA{i:06d}": [str(100 + i), str(200 + i)] for i in range(1, 5)}
    api = reporter_api(projects, links)
    connector = make(ReporterConnector, config, api)
    payloads, report = collect(connector, connector.plan(IngestOptions(), None, TODAY))
    total = sum(
        len(ReporterConnector.parse(p)["reporter_links"]) for p in payloads if p.kind == "links"
    )
    assert total == 8 and report.complete
    assert LINK_LIMIT == 10_000


# --- ClinicalTrials.gov -------------------------------------------------------------------------


def test_ctgov_scrubs_contacts() -> None:
    study = scrub_contacts(ctgov_study("NCT01234561"))
    text = json.dumps(study)
    assert (
        "pat@example.edu" not in text and "555-0100" not in text and "site@example.edu" not in text
    )
    assert "Ada Okafor, MD" in text  # officials are kept


def test_ctgov_parse_study() -> None:
    record = parse_study(ctgov_study("NCT01234561", pmids=("31000001", "31000002")))
    assert record["nct_id"] == "NCT01234561" and record["aliases"] == ["NCT00000000"]
    assert record["start_date"] == date(2024, 3, 1) and record["start_date_type"] == "ACTUAL"
    assert record["phases"] == ["PHASE2"] and record["enrollment"] == 120
    assert record["lead_sponsor"] == "Example University"
    assert record["collaborators"] == ["National Cancer Institute (NCI)"]
    assert [r["pmid"] for r in record["references"]] == ["31000001", "31000002"]
    assert [
        (s["type"], s["core_project_num"], s["ic_serial"]) for s in record["secondary_ids"]
    ] == [
        ("NIH", "R01CA123456", "CA123456"),
        ("NIH", "P30CA015704", "CA015704"),
        ("OTHER", None, None),
    ]
    assert record["sites"][0]["latitude"] == 1.5
    assert record["officials"][0]["role"] == "PRINCIPAL_INVESTIGATOR"


def test_ctgov_org_expression(config: Config) -> None:
    expression = org_expression(config)
    assert (
        'AREA[LeadSponsorName]("Example University" OR "Example University Medical Center")'
        in expression
    )
    assert "AREA[OverallOfficialAffiliation]" in expression


def test_ctgov_plan_incremental(config: Config) -> None:
    connector = CtgovConnector(None, config)  # type: ignore[arg-type]
    first = connector.plan(IngestOptions(), None, TODAY)
    assert first.params["advanced"] is None
    later = connector.plan(IngestOptions(), "2026-11-01", TODAY)
    assert later.params["advanced"] == "AREA[LastUpdatePostDate]RANGE[2026-10-31,2026-11-07]"
    with pytest.raises(TesseraError, match="Invalid NCT"):
        connector.plan(IngestOptions(ids=["NCT123"]), None, TODAY)


def test_ctgov_paging_and_scrubbed_payloads(config: Config) -> None:
    studies = [ctgov_study(f"NCT0000000{i}") for i in range(1, 6)]
    api = ctgov_api(studies, page_size=2)
    connector = make(CtgovConnector, config, api)
    payloads, report = collect(connector, connector.plan(IngestOptions(), None, TODAY))
    assert [len(p.json()["studies"]) for p in payloads] == [2, 2, 1]
    assert report.total_found == 5 and report.complete
    assert report.data_version == "2025-11-06T09:00:00"
    assert all(b'example.edu"' not in p.content for p in payloads)
    assert all(b"pat@example.edu" not in p.content for p in payloads)
    params = dict(api.calls("studies")[0].url.params)
    assert params["fields"] == "ProtocolSection,DerivedSection,HasResults"


def test_ctgov_ids_report_missing(config: Config) -> None:
    api = ctgov_api([ctgov_study("NCT00000001")])
    connector = make(CtgovConnector, config, api)
    plan = connector.plan(IngestOptions(ids=["nct00000001", "NCT99999999"]), None, TODAY)
    payloads, report = collect(connector, plan)
    assert len(payloads) == 1
    assert any("NCT99999999" in n for n in report.notes)


# --- iCite -----------------------------------------------------------------------------------


def test_icite_parse() -> None:
    data = {
        "data": [
            {
                "pmid": 5,
                "year": "2024",
                "citation_count": 3,
                "relative_citation_ratio": None,
                "apt": 0.05,
                "is_clinical": "Yes",
                "is_research_article": False,
            },
            {"pmid": None},
        ]
    }
    payload = Payload("pubs", json.dumps(data).encode(), "json", datetime.now(UTC))
    [record] = ICiteConnector.parse(payload)["icite"]
    assert record["pmid"] == "5" and record["year"] == 2024
    assert record["relative_citation_ratio"] is None
    assert (record["is_clinical"], record["is_research_article"]) == (True, False)


# --- ORCID -----------------------------------------------------------------------------------

AVERY = "0000-0003-9000-0014"
BLAKE = "0000-0003-9000-0022"


def test_orcid_checksum() -> None:
    assert orcid_checksum_ok(AVERY) and orcid_checksum_ok("0000-0002-1694-233X")
    assert not orcid_checksum_ok("0000-0003-9000-0015")
    assert valid_orcids(["https://orcid.org/0000-0003-9000-0022", AVERY, "nonsense", AVERY]) == [
        AVERY,
        BLAKE,
    ]


def test_orcid_parse_record() -> None:
    record = orcid_record(
        AVERY,
        works=(
            {"pmid": "31000001", "doi": "10.1000/ABC.1", "title": "First"},
            {"doi": "https://doi.org/10.1000/xyz", "pmc": "PMC123", "year": 2021},
        ),
        employments=({"department": "Medicine", "role": "Professor", "start": 2012},),
    )
    payload = Payload("record", json.dumps(record).encode(), "json", datetime.now(UTC))
    [row] = OrcidConnector.parse(payload)["orcid"]
    assert (row["orcid"], row["status"], row["family_name"]) == (AVERY, "public", "Example")
    assert row["other_names"] == ["A. Example"]
    first, second = row["works"]
    assert (first["pmid"], first["doi"], first["title"]) == ("31000001", "10.1000/abc.1", "First")
    assert (second["pmid"], second["doi"], second["pmcid"]) == (None, "10.1000/xyz", "PMC123")
    [job] = row["employments"]
    assert (job["organization"], job["department"], job["start_year"]) == (
        "Example University",
        "Medicine",
        2012,
    )
    assert (job["org_id_source"], job["end_year"]) == ("ROR", None)


def test_orcid_fetch_keeps_unavailable_records_as_data(config: Config) -> None:
    api = orcid_api({AVERY: orcid_record(AVERY)}, deprecated={BLAKE: AVERY})
    missing = "0000-0003-9000-0030"
    connector = make(OrcidConnector, config, api)
    plan = connector.plan(IngestOptions(mode="ids", ids=[AVERY, BLAKE, missing]), None, TODAY)
    payloads, report = collect(connector, plan)
    assert report.complete and report.payloads == 3
    rows = {r["orcid"]: r for p in payloads for r in OrcidConnector.parse(p)["orcid"]}
    assert rows[AVERY]["status"] == "public"
    assert (rows[BLAKE]["status"], rows[BLAKE]["primary_orcid"]) == ("deprecated", AVERY)
    assert rows[missing]["status"] == "not_found"
    assert all(r.headers["Accept"] == "application/json" for r in api.requests)


def test_orcid_server_errors_fail_the_batch(config: Config) -> None:
    api = orcid_api({})
    api.route("/record", lambda request: httpx.Response(503, text="maintenance"))
    connector = make(OrcidConnector, config, api)
    plan = connector.plan(IngestOptions(mode="ids", ids=[AVERY]), None, TODAY)
    payloads, report = collect(connector, plan)
    assert payloads == [] and not report.complete


# --- PubMed people mode ----------------------------------------------------------------------


def test_person_terms() -> None:
    from tessera.connectors.pubmed import person_terms

    broad, narrow = person_terms([("Okafor", "Ada B", "AB"), ("Okafor", "A", "A")])
    assert narrow == '"Okafor, Ada B"[fau] OR "Okafor, Ada"[fau]'
    assert broad == narrow + ' OR "Okafor AB"[au]'  # never a lone initial when a name is known
    assert person_terms([("Okafor", "", "A")]) == ('"Okafor A"[au]', '"Okafor A"[au]')
    assert person_terms([("Okafor", "A B", "AB")]) == ('"Okafor AB"[au]', '"Okafor AB"[au]')
    assert person_terms([("", "Ada", "A")]) is None


def test_pubmed_people_mode_splits_and_skips_common_names(config: Config) -> None:
    from tessera.connectors.pubmed import person_terms

    common = person_terms([("Wang", "Wei", "W")])
    terms = [person_terms([(f"Rare{i}", "Ada", "A")]) for i in range(11)] + [common]

    def esearch(request: httpx.Request) -> httpx.Response:
        term = form(request)["term"]
        count = 3 * len(set(re.findall(r"Rare\d+", term)))
        if "Wang" in term:
            count += 6000  # too common even by full name; big enough to split its batch
        return httpx.Response(200, content=esearch_xml(count, webenv=str(abs(hash(term)))))

    api = pubmed_api()
    api.route("esearch.fcgi", esearch)
    connector = make(PubMedConnector, config, api)
    plan = connector.plan(IngestOptions(mode="people", terms=terms), None, TODAY)
    assert plan.mode == "people" and plan.scope.startswith("people:")
    payloads, report = collect(connector, plan)
    assert report.total_found == 33  # 11 rare names x 3 papers
    assert any("too many papers" in n for n in report.notes)
    assert report.complete  # skipping a common name is deliberate, not a failure
    assert payloads
    empty = connector.plan(IngestOptions(mode="people"), None, TODAY)
    assert empty.empty
