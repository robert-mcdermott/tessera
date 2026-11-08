"""Fake public APIs for offline tests (synthetic data only)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx

FIXTURES = Path(__file__).parent / "fixtures"
EFETCH_XML = (FIXTURES / "pubmed_efetch.xml").read_bytes()

Handler = Callable[[httpx.Request], httpx.Response]


class FakeApi:
    """Route requests by path to handlers; record every request."""

    def __init__(self) -> None:
        self.routes: dict[str, Handler] = {}
        self.requests: list[httpx.Request] = []

    def route(self, path: str, handler: Handler) -> FakeApi:
        self.routes[path] = handler
        return self

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for path, handler in self.routes.items():
            if request.url.path.endswith(path):
                return handler(request)
        return httpx.Response(404, text=f"no fake route for {request.url.path}")

    def calls(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith(path)]


def form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def body(request: httpx.Request) -> Any:
    return json.loads(request.content)


def no_sleep(_: float) -> None:
    return None


# --- PubMed --------------------------------------------------------------------------------


def esearch_xml(
    count: int, webenv: str = "WE1", key: str = "1", warning: str | None = None
) -> bytes:
    warn = (
        f"<WarningList><PhraseNotFound>{warning}</PhraseNotFound></WarningList>" if warning else ""
    )
    return (
        f"<eSearchResult><Count>{count}</Count><RetMax>0</RetMax><QueryKey>{key}</QueryKey>"
        f"<WebEnv>{webenv}</WebEnv><QueryTranslation>translated</QueryTranslation>{warn}"
        "</eSearchResult>"
    ).encode()


def pubmed_api(count: int = 4, efetch: Handler | None = None) -> FakeApi:
    api = FakeApi()
    api.route("esearch.fcgi", lambda r: httpx.Response(200, content=esearch_xml(count)))
    api.route("efetch.fcgi", efetch or (lambda r: httpx.Response(200, content=EFETCH_XML)))
    return api


# --- RePORTER ------------------------------------------------------------------------------


def reporter_project(appl_id: int, core: str, fy: int = 2025, pi: int = 9001) -> dict[str, Any]:
    return {
        "appl_id": appl_id,
        "subproject_id": None,
        "fiscal_year": fy,
        "project_num": f"5{core}-0{fy - 2020}",
        "core_project_num": core,
        "project_title": f"Project {core}",
        "abstract_text": "  An abstract.  ",
        "phr_text": "Public health relevance.",
        "activity_code": core[:3],
        "agency_ic_admin": {
            "code": "CA",
            "abbreviation": "NCI",
            "name": "National Cancer Institute",
        },
        "funding_mechanism": "Non-SBIR/STTR",
        "award_amount": 500000.0,
        "direct_cost_amt": 350000,
        "indirect_cost_amt": 150000,
        "award_notice_date": "2025-06-01T00:00:00",
        "project_start_date": "2021-07-01T00:00:00",
        "project_end_date": "2026-06-30T00:00:00",
        "budget_start": "2025-07-01T00:00:00",
        "budget_end": "2026-06-30T00:00:00",
        "is_active": True,
        "is_new": False,
        "organization": {
            "org_name": "EXAMPLE  UNIVERSITY",
            "org_city": "EXAMPLE CITY",
            "org_state": "EX",
            "org_country": "UNITED STATES",
            "primary_uei": "EXAMPLEUEI01",
            "dept_type": "INTERNAL MEDICINE/MEDICINE",
        },
        "principal_investigators": [
            {
                "profile_id": pi,
                "first_name": "Ada",
                "middle_name": "B",
                "last_name": "Okafor",
                "full_name": "Ada B  Okafor",
                "is_contact_pi": True,
                "title": "PROFESSOR",
            },
            {
                "profile_id": None,
                "first_name": "No",
                "last_name": "Profile",
                "full_name": "No Profile",
            },
        ],
        "pref_terms": "Mosaics;Tiles; Research",
        "terms": "<ignored>",
        "spending_categories_desc": "Cancer; Genetics",
        "date_added": "2025-06-05T00:00:00",
    }


def reporter_api(
    projects: list[dict[str, Any]], links: dict[str, list[str]], fail_cores: set[str] | None = None
) -> FakeApi:
    api = FakeApi()
    fail_cores = fail_cores or set()

    def projects_search(request: httpx.Request) -> httpx.Response:
        payload = body(request)
        criteria, offset, limit = payload["criteria"], payload["offset"], payload["limit"]
        if payload.get("sort_field") == "date_added":
            return httpx.Response(
                200,
                json={
                    "meta": {"total": 1},
                    "results": [{"appl_id": 1, "date_added": "2025-11-01T00:00:00"}],
                },
            )
        matches = projects
        if "appl_ids" in criteria:
            matches = [p for p in projects if p["appl_id"] in criteria["appl_ids"]]
        if "fiscal_years" in criteria:
            matches = [p for p in matches if p["fiscal_year"] in criteria["fiscal_years"]]
        matches = sorted(matches, key=lambda p: p["appl_id"])
        return httpx.Response(
            200, json={"meta": {"total": len(matches)}, "results": matches[offset : offset + limit]}
        )

    def publications_search(request: httpx.Request) -> httpx.Response:
        payload = body(request)
        cores = payload["criteria"]["core_project_nums"]
        if fail_cores & set(cores):
            return httpx.Response(500, json={"message": "server error"})
        rows = [
            {"pmid": int(p), "coreproject": c, "applid": 1} for c in cores for p in links.get(c, [])
        ]
        offset, limit = payload["offset"], payload["limit"]
        return httpx.Response(
            200, json={"meta": {"total": len(rows)}, "results": rows[offset : offset + limit]}
        )

    api.route("projects/search", projects_search)
    api.route("publications/search", publications_search)
    return api


# --- ClinicalTrials.gov ----------------------------------------------------------------------


def ctgov_study(nct: str, pmids: tuple[str, ...] = ("31000001",)) -> dict[str, Any]:
    return {
        "protocolSection": {
            "identificationModule": {
                "nctId": nct,
                "nctIdAliases": ["NCT00000000"] if nct.endswith("1") else [],
                "orgStudyIdInfo": {"id": "EXU-2024-01"},
                "secondaryIdInfos": [
                    {"id": "R01CA123456", "type": "NIH"},
                    {"id": "P30 CA015704-45", "type": "NIH"},
                    {"id": "EX-LOCAL-9", "type": "OTHER"},
                ],
                "briefTitle": f"Study {nct}",
                "officialTitle": "An official title",
            },
            "statusModule": {
                "overallStatus": "RECRUITING",
                "startDateStruct": {"date": "2024-03", "type": "ACTUAL"},
                "primaryCompletionDateStruct": {"date": "2027-12-31", "type": "ESTIMATED"},
                "studyFirstPostDateStruct": {"date": "2024-02-01"},
                "lastUpdatePostDateStruct": {"date": "2025-10-01"},
            },
            "sponsorCollaboratorsModule": {
                "leadSponsor": {"name": "Example University", "class": "OTHER"},
                "collaborators": [{"name": "National Cancer Institute (NCI)", "class": "NIH"}],
            },
            "descriptionModule": {"briefSummary": "A summary."},
            "conditionsModule": {"conditions": ["Mosaic Syndrome"], "keywords": ["tiles"]},
            "designModule": {
                "studyType": "INTERVENTIONAL",
                "phases": ["PHASE2"],
                "enrollmentInfo": {"count": 120, "type": "ESTIMATED"},
            },
            "armsInterventionsModule": {"interventions": [{"type": "DRUG", "name": "Examplumab"}]},
            "contactsLocationsModule": {
                "centralContacts": [
                    {"name": "Pat Contact", "phone": "555-0100", "email": "pat@example.edu"}
                ],
                "overallOfficials": [
                    {
                        "name": "Ada Okafor, MD",
                        "affiliation": "Example University",
                        "role": "PRINCIPAL_INVESTIGATOR",
                    }
                ],
                "locations": [
                    {
                        "facility": "Example University Medical Center",
                        "status": "RECRUITING",
                        "city": "Example City",
                        "state": "EX",
                        "country": "United States",
                        "contacts": [{"name": "Site Person", "email": "site@example.edu"}],
                        "geoPoint": {"lat": 1.5, "lon": -2.5},
                    },
                ],
            },
            "referencesModule": {
                "references": [
                    {"pmid": p, "type": "RESULT", "citation": "A citation."} for p in pmids
                ]
                + [
                    {"pmid": pmids[0], "type": "RESULT", "citation": "Duplicate."},
                    {"citation": "No PMID"},
                ]
            },
        },
        "derivedSection": {
            "conditionBrowseModule": {"meshes": [{"id": "D000001", "term": "Mosaicism"}]},
            "interventionBrowseModule": {"meshes": []},
        },
        "hasResults": False,
    }


def ctgov_api(studies: list[dict[str, Any]], page_size: int = 2) -> FakeApi:
    api = FakeApi()
    api.route(
        "version",
        lambda r: httpx.Response(
            200, json={"apiVersion": "2.0", "dataTimestamp": "2025-11-06T09:00:00"}
        ),
    )

    def search(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        matches = studies
        if "filter.ids" in params:
            wanted = set(params["filter.ids"].split(","))
            matches = [
                s
                for s in studies
                if s["protocolSection"]["identificationModule"]["nctId"] in wanted
            ]
        start = int(params.get("pageToken", "0"))
        size = min(int(params.get("pageSize", 1000)), page_size)
        page = matches[start : start + size]
        data: dict[str, Any] = {"studies": json.loads(json.dumps(page))}
        if "countTotal" in params:
            data["totalCount"] = len(matches)
        if start + size < len(matches):
            data["nextPageToken"] = str(start + size)
        return httpx.Response(200, json=data)

    api.route("studies", search)
    return api


# --- iCite ---------------------------------------------------------------------------------


def icite_api() -> FakeApi:
    api = FakeApi()

    def pubs(request: httpx.Request) -> httpx.Response:
        pmids = request.url.params["pmids"].split(",")
        data = [
            {
                "pmid": int(p),
                "year": 2024,
                "citation_count": 10,
                "relative_citation_ratio": 1.5,
                "nih_percentile": 70.0,
                "apt": 0.25,
                "is_clinical": "No",
                "is_research_article": "Yes",
            }
            for p in pmids
            if p != "31000003"  # iCite has no record for very new papers
        ]
        return httpx.Response(200, json={"meta": {}, "data": data})

    api.route("pubs", pubs)
    return api
