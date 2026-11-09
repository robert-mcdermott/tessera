"""Publications, grants, and studies: lists (from the index) and details (lake + ledger)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from tessera.api.common import (
    Page,
    assertion_view,
    config_of,
    engine_of,
    envelope,
    index_of,
    lake_of,
    page,
    pick_fields,
    publication_summaries,
    split_values,
)
from tessera.core.clock import isoformat
from tessera.evidence import store
from tessera.evidence.orgmatch import OrgMatcher
from tessera.lake import Lake

router = APIRouter(prefix="/api/v1", tags=["records"])

METRIC_DEFINITIONS = {
    "citation_count": "Citations to this paper counted by NIH iCite.",
    "relative_citation_ratio": "Field-normalized citation rate (1.0 = the median NIH-funded "
    "paper in its field). A citation measure, not a quality score.",
    "nih_percentile": "Percentile of the Relative Citation Ratio among NIH-funded papers.",
    "apt": "Approximate Potential to Translate: an estimate of the chance that clinical "
    "articles will cite this paper. Not evidence of patient benefit.",
    "is_clinical": "Whether iCite classifies the paper as clinical research.",
}
REGISTRY_NOTE = (
    "Registry status is the study's overall status in ClinicalTrials.gov. It is "
    "not the organization's local recruitment status; check the official record."
)


def _provenance(row: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "source": source,
        "run_id": row.get("_run_id"),
        "retrieved_at": isoformat(row["_retrieved_at"]) if row.get("_retrieved_at") else None,
        "source_version": row.get("_source_version"),
    }


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if not k.startswith("_")}


def _one(lake: Lake, sql: str, params: list[Any]) -> dict[str, Any] | None:
    rows = lake.rows(sql, params)
    return rows[0] if rows else None


def _list(
    request: Request,
    kind: str,
    p: Page,
    q: str | None,
    filters: dict[str, list[str]],
    sort: str | None,
    fields: str | None,
) -> dict[str, Any]:
    result = index_of(request).search(kind, q, filters, sort, p.limit, p.offset)
    items = [{**i["summary"], "highlights": i["highlights"]} for i in result.items]
    return envelope(
        pick_fields(items, fields),
        total=result.total,
        limit=p.limit,
        next_cursor=p.next_cursor(result.total),
        facets=result.facets,
    )


# --- publications -----------------------------------------------------------------------------


@router.get(
    "/publications",
    tags=["publications"],
    summary="The organization's publications, with search and facets",
)
def list_publications(
    request: Request,
    p: Annotated[Page, Depends(page)],
    q: str | None = None,
    year: Annotated[list[str] | None, Query()] = None,
    journal: Annotated[list[str] | None, Query()] = None,
    type_: Annotated[list[str] | None, Query(alias="type")] = None,
    person: Annotated[list[str] | None, Query(description="Person IDs")] = None,
    flag: Annotated[list[str] | None, Query(description="e.g. retracted")] = None,
    sort: str | None = Query(None, description="relevance | year | citations"),
    fields: str | None = None,
) -> dict[str, Any]:
    filters = {
        "year": split_values(year),
        "journal": split_values(journal),
        "type": split_values(type_),
        "person": split_values(person),
        "flag": split_values(flag),
    }
    return _list(request, "publication", p, q, filters, sort, fields)


@router.get(
    "/publications/{pmid}",
    tags=["publications"],
    summary="A publication with its authors, people, grants, studies, and metrics",
)
def get_publication(request: Request, pmid: str) -> dict[str, Any]:
    if not pmid.isdigit():
        raise HTTPException(status_code=404, detail="No such publication.")
    lake, engine, config = lake_of(request), engine_of(request), config_of(request)
    article = _one(lake, "SELECT * FROM pubmed_article WHERE pmid = ?", [pmid])
    if article is None:
        raise HTTPException(status_code=404, detail="No such publication.")
    authors = lake.rows("SELECT * FROM pubmed_author WHERE pmid = ? ORDER BY position", [pmid])
    mesh = lake.rows(
        "SELECT descriptor_ui, descriptor, major_topic, qualifiers FROM pubmed_mesh "
        "WHERE pmid = ? ORDER BY descriptor",
        [pmid],
    )
    grants = lake.rows(
        "SELECT seq, grant_id_raw, core_project_num, agency FROM pubmed_grant "
        "WHERE pmid = ? ORDER BY seq",
        [pmid],
    )
    metrics = _one(lake, "SELECT * FROM icite_metrics WHERE pmid = ?", [pmid])

    ref = f"pub:{pmid}"
    authored = [
        a
        for a in store.assertions_for(
            engine, predicate="authored", object_id=ref, reported_only=False
        )
        if a["reported"] or a["locked"]
    ]
    people = store.display_names(engine, [a["subject_id"] for a in authored])
    by_position: dict[int, list[dict[str, Any]]] = {}
    for a in authored:
        position = (a["qualifiers"] or {}).get("author_position")
        by_position.setdefault(position, []).append(
            {
                "person": {"id": a["subject_id"], "name": people.get(a["subject_id"])},
                "assertion": assertion_view(a, config),
            }
        )
    acks = store.assertions_for(engine, predicate="acknowledges", subject_id=ref)
    refs = store.assertions_for(engine, predicate="references", object_id=ref)
    index = index_of(request)
    try:
        linked = index.summaries([a["object_id"] for a in acks] + [a["subject_id"] for a in refs])
    except Exception:
        linked = {}
    return envelope(
        {
            **_clean(article),
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "doi_url": f"https://doi.org/{article['doi']}" if article.get("doi") else None,
            "authors": [
                {**_clean(a), "people": by_position.get(a["position"], [])} for a in authors
            ],
            "unplaced_people": by_position.get(None, []),
            "mesh": mesh,
            "grants_acknowledged": grants,
            "grants": [
                {
                    "assertion": assertion_view(a, config),
                    "summary": linked.get(a["object_id"], {"id": a["object_id"]}),
                }
                for a in acks
            ],
            "studies": [
                {
                    "assertion": assertion_view(a, config),
                    "summary": linked.get(a["subject_id"], {"id": a["subject_id"]}),
                }
                for a in refs
            ],
            "metrics": (
                {
                    **_clean(metrics),
                    "definitions": METRIC_DEFINITIONS,
                    "provenance": _provenance(metrics, "icite"),
                }
                if metrics
                else None
            ),
            "provenance": _provenance(article, "pubmed"),
        }
    )


# --- grants -----------------------------------------------------------------------------------


@router.get("/grants", tags=["grants"], summary="The organization's grants, with search and facets")
def list_grants(
    request: Request,
    p: Annotated[Page, Depends(page)],
    q: str | None = None,
    status: Annotated[list[str] | None, Query(description="active, ended")] = None,
    activity: Annotated[list[str] | None, Query()] = None,
    institute: Annotated[list[str] | None, Query()] = None,
    year: Annotated[list[str] | None, Query()] = None,
    person: Annotated[list[str] | None, Query()] = None,
    sort: str | None = Query(None, description="relevance | year | amount"),
    fields: str | None = None,
) -> dict[str, Any]:
    filters = {
        "status": split_values(status),
        "activity": split_values(activity),
        "institute": split_values(institute),
        "year": split_values(year),
        "person": split_values(person),
    }
    return _list(request, "grant", p, q, filters, sort, fields)


@router.get(
    "/grants/{core_project_num}",
    tags=["grants"],
    summary="A grant: fiscal-year records, investigators, publications, and studies",
)
def get_grant(request: Request, core_project_num: str) -> dict[str, Any]:
    core = core_project_num.upper()
    lake, engine, config = lake_of(request), engine_of(request), config_of(request)
    years = lake.rows(
        "SELECT * FROM reporter_project WHERE core_project_num = ? ORDER BY fiscal_year, appl_id",
        [core],
    )
    if not years:
        raise HTTPException(status_code=404, detail="No such grant.")
    latest = ([y for y in years if not y["subproject_id"]] or years)[-1]  # not a subproject
    investigators = lake.rows(
        "SELECT DISTINCT i.profile_id, i.full_name, i.is_contact_pi FROM reporter_investigator i "
        "JOIN reporter_project p USING (appl_id) WHERE p.core_project_num = ?",
        [core],
    )
    profile_to_person = store.people_by_identifier(
        engine, "nih_profile", [i["profile_id"] for i in investigators]
    )
    ref = f"grt:{core}"
    pubs = store.assertions_for(engine, predicate="acknowledges", object_id=ref)
    studies = store.assertions_for(engine, predicate="funded_by", object_id=ref)
    pub_summaries = publication_summaries(
        index_of(request), lake, [a["subject_id"].removeprefix("pub:") for a in pubs]
    )
    try:
        study_summaries = index_of(request).summaries([a["subject_id"] for a in studies])
    except Exception:
        study_summaries = {}
    parent = [y for y in years if not y["subproject_id"]] or years
    publications = sorted(
        (
            {
                "assertion": assertion_view(a, config),
                "summary": pub_summaries.get(
                    a["subject_id"].removeprefix("pub:"),
                    {"pmid": a["subject_id"].removeprefix("pub:")},
                ),
            }
            for a in pubs
        ),
        key=lambda i: -(i["summary"].get("year") or 0),
    )
    return envelope(
        {
            "core_project_num": core,
            "title": latest["title"],
            "abstract": latest["abstract"],
            "public_health_relevance": latest["phr"],
            "activity_code": latest["activity_code"],
            "institute": latest["agency_ic_admin"],
            "organization": latest["org_name"],
            "project_start": latest["project_start"],
            "project_end": latest["project_end"],
            "is_active": latest["is_active"],
            "terms": latest["terms"] or [],
            "fiscal_years": [
                {
                    "fiscal_year": y["fiscal_year"],
                    "appl_id": y["appl_id"],
                    "project_num": y["project_num"],
                    "subproject_id": y["subproject_id"],
                    "award_amount": y["award_amount"],
                    "direct_cost": y["direct_cost"],
                    "indirect_cost": y["indirect_cost"],
                    "budget_start": y["budget_start"],
                    "budget_end": y["budget_end"],
                }
                for y in years
            ],
            "totals": {
                "total_awarded": sum(y["award_amount"] or 0 for y in parent),
                "counting_rule": "Total-cost award amounts summed over this grant's fiscal-year "
                "records in the data, excluding subproject records. Not "
                "expenditures, and not the organization's share of multi-site awards.",
            },
            "investigators": [
                {
                    "profile_id": i["profile_id"],
                    "name": i["full_name"],
                    "contact_pi": i["is_contact_pi"],
                    "person_id": profile_to_person.get(str(i["profile_id"])),
                }
                for i in investigators
            ],
            "publications": publications,
            "studies": [
                {
                    "assertion": assertion_view(a, config),
                    "summary": study_summaries.get(a["subject_id"], {"id": a["subject_id"]}),
                }
                for a in studies
            ],
            "url": f"https://reporter.nih.gov/project-details/{latest['appl_id']}",
            "provenance": _provenance(latest, "reporter"),
        }
    )


# --- studies ----------------------------------------------------------------------------------


@router.get(
    "/studies", tags=["studies"], summary="The organization's studies, with search and facets"
)
def list_studies(
    request: Request,
    p: Annotated[Page, Depends(page)],
    q: str | None = None,
    status: Annotated[list[str] | None, Query(description="Registry status")] = None,
    phase: Annotated[list[str] | None, Query()] = None,
    type_: Annotated[list[str] | None, Query(alias="type")] = None,
    year: Annotated[list[str] | None, Query()] = None,
    person: Annotated[list[str] | None, Query()] = None,
    sort: str | None = Query(None, description="relevance | year"),
    fields: str | None = None,
) -> dict[str, Any]:
    filters = {
        "status": split_values(status),
        "phase": split_values(phase),
        "type": split_values(type_),
        "year": split_values(year),
        "person": split_values(person),
    }
    return _list(request, "study", p, q, filters, sort, fields)


@router.get(
    "/studies/{nct_id}",
    tags=["studies"],
    summary="A study: registry details, officials, sites, publications, and grants",
)
def get_study(request: Request, nct_id: str) -> dict[str, Any]:
    nct_id = nct_id.upper()
    lake, engine, config = lake_of(request), engine_of(request), config_of(request)
    study = _one(lake, "SELECT * FROM ctgov_study WHERE nct_id = ?", [nct_id])
    if study is None:
        raise HTTPException(status_code=404, detail="No such study.")
    matcher = OrgMatcher(config.organization)
    officials = lake.rows("SELECT * FROM ctgov_official WHERE nct_id = ? ORDER BY seq", [nct_id])
    sites = lake.rows("SELECT * FROM ctgov_site WHERE nct_id = ? ORDER BY seq", [nct_id])
    references = lake.rows("SELECT * FROM ctgov_reference WHERE nct_id = ?", [nct_id])
    ref = f"stu:{nct_id}"
    suggestions = [
        a
        for a in store.assertions_for(engine, predicate="official_of", object_id=ref)
        if a["status"] != "rejected"
    ]
    people = store.display_names(engine, [a["subject_id"] for a in suggestions])
    by_official: dict[str, list[dict[str, Any]]] = {}
    for a in sorted(suggestions, key=lambda a: -(a["score"] or 0)):
        name = (a["qualifiers"] or {}).get("official_name")
        by_official.setdefault(name, []).append(
            {
                "person": {"id": a["subject_id"], "name": people.get(a["subject_id"])},
                "assertion": assertion_view(a, config),
            }
        )
    funded = store.assertions_for(engine, predicate="funded_by", subject_id=ref)
    pubs = publication_summaries(index_of(request), lake, [r["pmid"] for r in references])
    try:
        grant_summaries = index_of(request).summaries([a["object_id"] for a in funded])
    except Exception:
        grant_summaries = {}
    return envelope(
        {
            **_clean(study),
            "registry_status": study["overall_status"],
            "registry_status_note": REGISTRY_NOTE,
            "url": f"https://clinicaltrials.gov/study/{nct_id}",
            "officials": [
                {**_clean(o), "people": by_official.get(o["name"], [])} for o in officials
            ],
            "sites": [
                {**_clean(s), "is_organization": matcher.matches(s["facility"])} for s in sites
            ],
            "organization_sites": sum(1 for s in sites if matcher.matches(s["facility"])),
            "publications": [
                {"reference_type": r["type"], "pmid": r["pmid"], "summary": pubs.get(r["pmid"])}
                for r in references
            ],
            "grants": [
                {
                    "assertion": assertion_view(a, config),
                    "summary": grant_summaries.get(a["object_id"], {"id": a["object_id"]}),
                }
                for a in funded
            ],
            "provenance": _provenance(study, "ctgov"),
        }
    )
