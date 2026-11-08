"""People: the directory and profiles."""

from __future__ import annotations

from collections import Counter
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from tessera.api.common import (
    Page,
    assertion_view,
    band,
    config_of,
    engine_of,
    envelope,
    index_of,
    lake_of,
    page,
    pick_fields,
    publication_summaries,
    refresh_index,
    split_values,
)
from tessera.core.clock import utc_now
from tessera.core.ids import new_id
from tessera.evidence import store
from tessera.ledger.tables import (
    assertion,
    curation_event,
    person,
    person_identifier,
    person_name,
    unit,
)

router = APIRouter(prefix="/api/v1/people", tags=["people"])

PUBLICATION_STATUSES = ("suggested", "accepted", "deferred", "rejected")
BANDS = ("strong", "possible", "unlikely")


@router.get("", summary="Directory of people, with search, facets, and sorting")
def list_people(
    request: Request,
    p: Annotated[Page, Depends(page)],
    q: str | None = Query(None, description="Free-text search"),
    status: Annotated[list[str] | None, Query(description="active, former")] = None,
    unit: Annotated[list[str] | None, Query(description="Unit names")] = None,
    sort: str | None = Query(None, description="name | activity | relevance"),
    fields: str | None = Query(None, description="Comma-separated item keys to return"),
) -> dict[str, Any]:
    filters = {"status": split_values(status), "unit": split_values(unit)}
    result = index_of(request).search("person", q, filters, sort, p.limit, p.offset)
    items = [{**item["summary"], "highlights": item["highlights"]} for item in result.items]
    return envelope(
        pick_fields(items, fields),
        total=result.total,
        limit=p.limit,
        next_cursor=p.next_cursor(result.total),
        facets=result.facets,
    )


def _person_row(request: Request, person_id: str) -> dict[str, Any]:
    with engine_of(request).connect() as conn:
        row = conn.execute(select(person).where(person.c.id == person_id)).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="No such person.")
    return dict(row)


@router.get("/{person_id}", summary="A person's profile, live from the ledger")
def get_person(request: Request, person_id: str) -> dict[str, Any]:
    config = config_of(request)
    engine = engine_of(request)
    row = _person_row(request, person_id)
    with engine.connect() as conn:
        names = [
            dict(r)
            for r in conn.execute(
                select(
                    person_name.c.family,
                    person_name.c.given,
                    person_name.c.kind,
                    person_name.c.source,
                ).where(person_name.c.person_id == person_id)
            ).mappings()
        ]
        identifiers = [
            dict(r)
            for r in conn.execute(
                select(
                    person_identifier.c.scheme,
                    person_identifier.c.value,
                    person_identifier.c.status,
                    person_identifier.c.source,
                ).where(person_identifier.c.person_id == person_id)
            ).mappings()
        ]
        units = {r.id: r.name for r in conn.execute(select(unit.c.id, unit.c.name))}
    links = store.assertions_for(engine, subject_id=person_id)
    authored = [a for a in links if a["predicate"] == "authored"]
    counts: dict[str, Any] = {
        "publications": dict(Counter(a["status"] for a in authored)),
        "suggested_by_band": dict(
            Counter(band(a["score"], config) for a in authored if a["status"] == "suggested")
        ),
        "grants": sum(1 for a in links if a["predicate"] == "investigator_on"),
        "studies": sum(
            1 for a in links if a["predicate"] == "official_of" and a["status"] != "rejected"
        ),
    }
    memberships = [
        {
            "unit": units.get(a["object_id"], a["object_id"]),
            "unit_id": a["object_id"],
            **(a["qualifiers"] or {}),
        }
        for a in links
        if a["predicate"] == "member_of"
    ]
    # Timeline: accepted papers and likely suggestions per year.
    likely = [
        a["object_id"].removeprefix("pub:")
        for a in authored
        if a["status"] == "accepted"
        or (a["status"] == "suggested" and (a["score"] or 0) >= config.evidence.review_threshold)
    ]
    summaries = publication_summaries(index_of(request), lake_of(request), likely)
    by_year: Counter = Counter()
    confirmed: Counter = Counter()
    accepted = {a["object_id"].removeprefix("pub:") for a in authored if a["status"] == "accepted"}
    for pmid in likely:
        year = (summaries.get(pmid) or {}).get("year")
        if year:
            by_year[year] += 1
            if pmid in accepted:
                confirmed[year] += 1
    try:
        concepts = index_of(request).summaries([person_id]).get(person_id, {}).get("concepts", [])
    except Exception:
        concepts = []
    return envelope(
        {
            "id": row["id"],
            "name": row["display_name"],
            "status": row["status"],
            "family_name": row["family_name"],
            "given_name": row["given_name"],
            "names": names,
            "identifiers": identifiers,
            "units": memberships,
            "counts": counts,
            "concepts": concepts,
            "timeline": [
                {"year": y, "publications": by_year[y], "accepted": confirmed[y]}
                for y in sorted(by_year)
            ],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )


@router.get("/{person_id}/publications", summary="A person's publications and suggestions")
def person_publications(
    request: Request,
    person_id: str,
    p: Annotated[Page, Depends(page)],
    status: Annotated[
        list[str] | None, Query(description="accepted, suggested, deferred, rejected")
    ] = None,
    band_: Annotated[
        list[str] | None, Query(alias="band", description="strong, possible, unlikely")
    ] = None,
    sort: str = Query("year", description="year | score"),
) -> dict[str, Any]:
    config = config_of(request)
    _person_row(request, person_id)
    statuses = split_values(status) or ["accepted", "suggested"]
    bands = split_values(band_) or ["strong", "possible"]
    bad = (set(statuses) - set(PUBLICATION_STATUSES)) | (set(bands) - set(BANDS))
    if bad:
        raise HTTPException(status_code=400, detail=f"Unknown status or band: {sorted(bad)}")
    rows = store.assertions_for(engine_of(request), subject_id=person_id, predicate="authored")
    tabs = Counter(
        "accepted"
        if a["status"] == "accepted"
        else f"suggested:{band(a['score'], config)}"
        if a["status"] == "suggested"
        else a["status"]
        for a in rows
    )
    chosen = [
        a
        for a in rows
        if a["status"] in statuses
        and (a["status"] != "suggested" or band(a["score"], config) in bands)
    ]
    summaries = publication_summaries(
        index_of(request), lake_of(request), [a["object_id"].removeprefix("pub:") for a in chosen]
    )

    def year_of(a: dict[str, Any]) -> int:
        return (summaries.get(a["object_id"].removeprefix("pub:")) or {}).get("year") or 0

    if sort == "score":
        chosen.sort(key=lambda a: (-(a["score"] or 0), -year_of(a)))
    else:
        chosen.sort(key=lambda a: (-year_of(a), -(a["score"] or 0)))
    window = chosen[p.offset : p.offset + p.limit]
    items = [
        {
            "assertion": assertion_view(a, config),
            "publication": summaries.get(
                a["object_id"].removeprefix("pub:"), {"pmid": a["object_id"].removeprefix("pub:")}
            ),
        }
        for a in window
    ]
    return envelope(
        items,
        total=len(chosen),
        limit=p.limit,
        next_cursor=p.next_cursor(len(chosen)),
        tabs=dict(tabs),
    )


class ManualPublication(BaseModel):
    pmid: str = Field(pattern=r"^[1-9][0-9]{0,8}$", description="PubMed ID")
    note: str | None = Field(None, max_length=1000)


@router.post(
    "/{person_id}/publications",
    status_code=201,
    summary="Add a missing publication to a person (fetched from PubMed if needed)",
)
def add_publication(request: Request, person_id: str, body: ManualPublication) -> dict[str, Any]:
    from tessera.connectors.base import IngestOptions
    from tessera.jobs.ingest import ingest

    config = config_of(request)
    engine = engine_of(request)
    _person_row(request, person_id)
    lake = lake_of(request)
    lake.refresh(force=True)
    if not lake.scalar("SELECT count(*) FROM pubmed_article WHERE pmid = ?", [body.pmid]):
        result = ingest(
            config,
            request.app.state.home,
            engine,
            "pubmed",
            IngestOptions(mode="ids", ids=[body.pmid]),
        )
        lake.refresh(force=True)
        if result.status == "failed" or not lake.scalar(
            "SELECT count(*) FROM pubmed_article WHERE pmid = ?", [body.pmid]
        ):
            raise HTTPException(status_code=404, detail=f"PubMed has no record {body.pmid}.")
    actor = config.server.operator
    now = utc_now()
    object_id = f"pub:{body.pmid}"
    with engine.begin() as conn:
        existing = (
            conn.execute(
                select(assertion).where(
                    assertion.c.subject_id == person_id,
                    assertion.c.predicate == "authored",
                    assertion.c.object_id == object_id,
                )
            )
            .mappings()
            .first()
        )
        if existing is not None:
            aid = existing["id"]
        else:
            aid = new_id("asr")
            conn.execute(
                assertion.insert().values(
                    id=aid,
                    subject_type="person",
                    subject_id=person_id,
                    predicate="authored",
                    object_type="publication",
                    object_id=object_id,
                    qualifier_key="",
                    qualifiers={},
                    origin="human",
                    method="manual/v1",
                    score=None,
                    evidence=[
                        {
                            "feature": "manual",
                            "value": actor,
                            "weight": None,
                            "explanation": f"Added by {actor}",
                            "source_ref": object_id,
                        }
                    ],
                    status="suggested",
                    locked=False,
                    reported=True,
                    first_seen_at=now,
                    last_evaluated_at=now,
                    updated_at=now,
                )
            )
    if existing is None or existing["status"] != "accepted":
        store.decide(engine, aid, "accept", actor, body.note)
    refresh_index(request, people=[person_id], records=[object_id])
    row = store.assertions_for(
        engine, subject_id=person_id, predicate="authored", reported_only=False
    )
    created = next(r for r in row if r["id"] == aid)
    return envelope(assertion_view(created, config))


def _linked(request: Request, person_id: str, predicate: str) -> list[dict[str, Any]]:
    _person_row(request, person_id)
    rows = [
        a
        for a in store.assertions_for(engine_of(request), subject_id=person_id, predicate=predicate)
        if a["status"] != "rejected"
    ]
    summaries = index_of(request).summaries([a["object_id"] for a in rows])
    config = config_of(request)
    return [
        {
            "assertion": assertion_view(a, config),
            "summary": summaries.get(a["object_id"], {"id": a["object_id"]}),
        }
        for a in rows
    ]


@router.get("/{person_id}/grants", summary="Grants the person is an investigator on")
def person_grants(request: Request, person_id: str) -> dict[str, Any]:
    items = _linked(request, person_id, "investigator_on")
    items.sort(key=lambda i: -((i["summary"].get("fiscal_years") or [0, 0])[1]))
    return envelope(items, total=len(items))


@router.get("/{person_id}/studies", summary="Studies the person may be an official of")
def person_studies(request: Request, person_id: str) -> dict[str, Any]:
    items = _linked(request, person_id, "official_of")
    items.sort(key=lambda i: -(i["assertion"]["score"] or 0))
    return envelope(items, total=len(items))


@router.get("/{person_id}/history", summary="Curation decisions about this person's links")
def person_history(
    request: Request, person_id: str, limit: int = Query(50, ge=1, le=500)
) -> dict[str, Any]:
    _person_row(request, person_id)
    with engine_of(request).connect() as conn:
        rows = (
            conn.execute(
                select(curation_event, assertion.c.predicate, assertion.c.object_id)
                .join(assertion, assertion.c.id == curation_event.c.assertion_id)
                .where(assertion.c.subject_id == person_id)
                .order_by(curation_event.c.id.desc())
                .limit(limit)
            )
            .mappings()
            .all()
        )
    return envelope([dict(r) for r in rows])


__all__ = ["router"]
