"""Funding opportunities (INT-09, INT-12): Grants.gov's, and those the organization tracks."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from tessera.api.access import principal_of
from tessera.api.common import (
    Page,
    config_of,
    engine_of,
    envelope,
    index_of,
    lake_of,
    page,
    pick_fields,
    split_values,
)
from tessera.insight.inputs import source_stamps
from tessera.opportunities import catalog, store
from tessera.opportunities.store import OpportunityError
from tessera.search.update import refresh_opportunities

router = APIRouter(prefix="/api/v1", tags=["opportunities"])


def _view(request: Request, oid: str) -> dict[str, Any]:
    found = catalog.get(engine_of(request), lake_of(request), oid)
    if found is None:
        raise HTTPException(status_code=404, detail="No such opportunity.")
    return found


def _local(request: Request, oid: str) -> str:
    found = _view(request, oid)
    if found["source"] != catalog.LOCAL:
        raise HTTPException(
            status_code=409,
            detail="This opportunity comes from Grants.gov; it changes only there.",
        )
    return catalog.id_of(found["id"])


def _refresh(request: Request, refs: list[str]) -> None:
    refresh_opportunities(request.app.state.home, engine_of(request), refs)


@router.get("/opportunities", summary="Funding opportunities, with search and facets")
def list_opportunities(
    request: Request,
    p: Annotated[Page, Depends(page)],
    q: str | None = None,
    status: Annotated[
        list[str] | None, Query(description="posted, forecasted, closed, archived")
    ] = None,
    source: Annotated[list[str] | None, Query(description="grants_gov or local")] = None,
    kind: Annotated[list[str] | None, Query(description="federal, foundation, internal, …")] = None,
    closing: Annotated[list[str] | None, Query(description="30 or 90 (days)")] = None,
    agency: Annotated[list[str] | None, Query()] = None,
    activity: Annotated[list[str] | None, Query()] = None,
    category: Annotated[list[str] | None, Query()] = None,
    sort: str | None = Query(None, description="closes | posted | relevance"),
    fields: str | None = None,
) -> dict[str, Any]:
    filters = {
        "status": split_values(status),
        "source": split_values(source),
        "kind": split_values(kind),
        "closing": split_values(closing),
        "agency": split_values(agency),
        "activity": split_values(activity),
        "category": split_values(category),
    }
    result = index_of(request).search("opportunity", q, filters, sort, p.limit, p.offset)
    items = [{**i["summary"], "highlights": i["highlights"]} for i in result.items]
    return envelope(
        pick_fields(items, fields),
        total=result.total,
        limit=p.limit,
        next_cursor=p.next_cursor(result.total),
        facets=result.facets,
    )


@router.get("/opportunities/{oid}", summary="An opportunity: dates, money, eligibility, and text")
def get_opportunity(request: Request, oid: str) -> dict[str, Any]:
    found = _view(request, oid)
    stamps = source_stamps(engine_of(request), ["grants_gov"], config_of(request))
    return envelope({**found, "sources": stamps if found["source"] == catalog.FEDERAL else []})


@router.post("/opportunities", summary="Add an opportunity the organization tracks")
def add_opportunity(request: Request, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    try:
        oid = store.create(engine_of(request), body, principal_of(request).name)
    except OpportunityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _refresh(request, [catalog.ref_of(oid)])
    return envelope(_view(request, oid))


@router.post("/opportunities/import", summary="Add or update many from CSV-like rows")
def import_opportunities(
    request: Request,
    rows: Annotated[list[dict[str, Any]] | None, Body(embed=True)] = None,
    csv: Annotated[str | None, Body(embed=True, description="CSV text with a header")] = None,
    source: Annotated[str | None, Body(embed=True)] = None,
    complete: Annotated[bool, Body(embed=True)] = False,
    dry_run: Annotated[bool, Body(embed=True)] = False,
) -> dict[str, Any]:
    engine = engine_of(request)
    if rows is None and csv is None:
        raise HTTPException(status_code=400, detail="Send rows, or CSV text with a header.")
    rows = rows if rows is not None else store.csv_rows(csv or "")
    try:
        result = store.import_rows(engine, rows, principal_of(request).name, source=source,
                                   complete=complete, dry_run=dry_run)  # fmt: skip
    except OpportunityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not dry_run and not result["problems"]:
        _rebuild_local(request)
    return envelope(result)


def _rebuild_local(request: Request) -> None:
    lake = lake_of(request)
    refs = [v["id"] for v in catalog.everything(engine_of(request), lake)
            if v["source"] == catalog.LOCAL]  # fmt: skip
    _refresh(request, refs)


@router.post("/opportunities/{oid}", summary="Change one the organization tracks, or restore it")
def change_opportunity(
    request: Request, oid: str, body: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    local = _local(request, oid)
    try:
        store.change(engine_of(request), local, body, principal_of(request).name)
    except OpportunityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _refresh(request, [catalog.ref_of(local)])
    return envelope(_view(request, local))


@router.delete("/opportunities/{oid}", summary="Archive one the organization tracks")
def archive_opportunity(request: Request, oid: str) -> dict[str, Any]:
    local = _local(request, oid)
    store.archive(engine_of(request), local, principal_of(request).name)
    _refresh(request, [catalog.ref_of(local)])
    return envelope(_view(request, local))
