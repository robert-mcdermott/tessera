"""Funding opportunities (INT-09, INT-12): Grants.gov's, and those the organization tracks."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy import select

from tessera.analytics.concepts import profile_path
from tessera.analytics.experts import ConceptReader, _exists
from tessera.analytics.vocabulary import vocabulary_path
from tessera.api import bodies
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
from tessera.evidence.store import display_names
from tessera.groups import store as groups_store
from tessera.insight.inputs import source_stamps
from tessera.ledger.tables import unit
from tessera.opportunities import catalog, eligibility, fit, store
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
    refresh_opportunities(config_of(request), request.app.state.home, engine_of(request), refs)


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
    eligible: Annotated[
        list[str] | None, Query(description="meets, check, does_not_meet (the organization)")
    ] = None,
    fits: str | None = Query(None, description="a person's ID, or me: opportunities they fit"),
    group: str | None = Query(None, description="a group: opportunities it fits"),
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
        "eligibility": split_values(eligible),
        "fits": [_person(request, fits)] if fits else [],
        "group": [_group(request, group)["id"]] if group else [],
        "closing": split_values(closing),
        "agency": split_values(agency),
        "activity": split_values(activity),
        "category": split_values(category),
    }
    result = index_of(request).search("opportunity", q, filters, sort, p.limit, p.offset)
    items = [{**i["summary"], "highlights": i["highlights"]} for i in result.items]
    mine = _viewer_fits(request)
    for item in items:
        item["fit"] = mine.get(item["id"])
    return envelope(
        pick_fields(items, fields),
        total=result.total,
        limit=p.limit,
        next_cursor=p.next_cursor(result.total),
        facets=result.facets,
    )


@router.get(
    "/opportunities/{opportunity_id}", summary="An opportunity: dates, money, eligibility, and text"
)
def get_opportunity(request: Request, opportunity_id: str) -> dict[str, Any]:
    oid = opportunity_id
    found = _view(request, oid)
    stamps = source_stamps(engine_of(request), ["grants_gov"], config_of(request))
    return envelope({**found, "sources": stamps if found["source"] == catalog.FEDERAL else []})


@router.post(
    "/opportunities",
    summary="Add an opportunity the organization tracks",
    openapi_extra=bodies.NEW_OPPORTUNITY,
)
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


@router.post(
    "/opportunities/{opportunity_id}",
    summary="Change one the organization tracks, or restore it",
    openapi_extra=bodies.OPPORTUNITY_CHANGE,
)
def change_opportunity(
    request: Request, opportunity_id: str, body: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    oid = opportunity_id
    local = _local(request, oid)
    try:
        store.change(engine_of(request), local, body, principal_of(request).name)
    except OpportunityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _refresh(request, [catalog.ref_of(local)])
    return envelope(_view(request, local))


@router.delete("/opportunities/{opportunity_id}", summary="Archive one the organization tracks")
def archive_opportunity(request: Request, opportunity_id: str) -> dict[str, Any]:
    oid = opportunity_id
    local = _local(request, oid)
    store.archive(engine_of(request), local, principal_of(request).name)
    _refresh(request, [catalog.ref_of(local)])
    return envelope(_view(request, local))


# --- who fits, whether the organization may apply, and one's own list ---------------------------


def _owner(request: Request) -> str:
    return principal_of(request).account_id or "operator"


def _person(request: Request, value: str) -> str:
    if value == "me":
        person_id = principal_of(request).person_id
        if not person_id:
            raise HTTPException(status_code=400, detail="Your account is not linked to a person.")
        return person_id
    return value


def _group(request: Request, ref: str) -> dict[str, Any]:
    with engine_of(request).connect() as conn:
        try:
            return groups_store.resolve(conn, ref)
        except groups_store.GroupNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


def _viewer_fits(request: Request) -> dict[str, dict[str, Any]]:
    person_id = principal_of(request).person_id
    if not person_id:
        return {}
    return {f["opportunity"]: {"band": f["band"], "label": f["band_label"]}
            for f in fit.fitting(request.app.state.home, "person", person_id)}  # fmt: skip


def _named(request: Request, found: dict[str, Any]) -> dict[str, Any]:
    """People and groups with their names (fit files keep IDs only)."""
    engine = engine_of(request)
    people_ids = [p["id"] for p in found["people"]]
    for g in found["groups"]:
        people_ids += g["members"]
    names = display_names(engine, people_ids)
    group_ids = [g["id"] for g in found["groups"]]
    query = select(unit.c.id, unit.c.name, unit.c.slug, unit.c.kind).where(unit.c.id.in_(group_ids))
    with engine.connect() as conn:
        groups = {r.id: r for r in conn.execute(query)}
    return {
        "concepts": found["concepts"],
        "people": [{**p, "name": names.get(p["id"], p["id"]),
                    "band_label": fit.BANDS.get(p["band"], "")} for p in found["people"]],
        "groups": [{**g, "name": groups[g["id"]].name if g["id"] in groups else g["id"],
                    "slug": groups[g["id"]].slug if g["id"] in groups else None,
                    "kind": groups[g["id"]].kind if g["id"] in groups else None,
                    "band_label": fit.BANDS.get(g["band"], ""),
                    "members": [{"id": m, "name": names.get(m, m)} for m in g["members"]]}
                   for g in found["groups"] if g["id"] in groups],
    }  # fmt: skip


@router.get(
    "/opportunities/{opportunity_id}/fit",
    summary="Who fits an opportunity and why, and whether the organization may apply",
)
def opportunity_fit(
    request: Request,
    opportunity_id: str,
    concept: Annotated[list[str] | None, Query(description="MeSH IDs to add")] = None,
    drop: Annotated[list[str] | None, Query(description="MeSH IDs to leave out")] = None,
) -> dict[str, Any]:
    oid = opportunity_id
    found_view = _view(request, oid)
    home = request.app.state.home
    today = date.today()
    added, dropped = split_values(concept), split_values(drop)
    stored = None if (added or dropped) else fit.fits_of(home, found_view["id"])
    if stored is None and _exists(profile_path(home)):
        lake = lake_of(request)
        reader = ConceptReader(lake, profile_path(home), vocabulary_path(home))
        live = fit.read_one(reader, index_of(request), found_view, added, dropped)
        members = fit._memberships(engine_of(request), today)
        stored = {**live, "groups": fit.group_fits(live["people"], members)}
    stored = stored or {"concepts": [], "people": [], "groups": []}
    interest = store.interests(engine_of(request), _owner(request)).get(found_view["id"])
    return envelope({
        **_named(request, stored),
        "eligibility": eligibility.check(found_view, config_of(request), today),
        "interest": interest,
        "adjusted": bool(added or dropped),
    })  # fmt: skip


@router.post(
    "/opportunities/{opportunity_id}/interest", summary="Watch an opportunity, or dismiss it"
)
def opportunity_interest(
    request: Request,
    opportunity_id: str,
    state: Annotated[str | None, Body(embed=True, description="watching, dismissed, or null")],
) -> dict[str, Any]:
    oid = opportunity_id
    found_view = _view(request, oid)
    try:
        store.set_interest(engine_of(request), _owner(request), found_view["id"], state)
    except OpportunityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return envelope({"id": found_view["id"], "interest": state})


def _listing(request: Request, fits: list[dict[str, Any]], hide: set[str]) -> list[dict[str, Any]]:
    """Open opportunities from a list of fits, with eligibility, dismissed ones left out."""
    today = date.today()
    wanted = [f for f in fits if f["opportunity"] not in hide]
    refs = [f["opportunity"] for f in wanted]
    views = {v["id"]: v for v in catalog.some(engine_of(request), lake_of(request), refs, today)}
    config = config_of(request)
    out = []
    for f in wanted:
        v = views.get(f["opportunity"])
        if v is None or v["status"] not in catalog.OPEN:
            continue
        verdict = eligibility.check(v, config, today)
        out.append({**{k: v[k] for k in ("id", "title", "number", "sponsor", "source",
                                        "source_label", "status", "status_label", "closes",
                                        "expected_post", "award_ceiling", "activity_codes")},
                    "fit": {"band": f["band"], "label": f["band_label"]},
                    "eligibility": {k: verdict[k] for k in ("overall", "label")}})  # fmt: skip
    # Those the organization may apply for first, then the strongest fits, soonest deadline.
    out.sort(key=lambda o: (o["eligibility"]["overall"] == "does_not_meet",
                            (o["fit"] or {}).get("band") != "strong",
                            o["closes"] or date.max, o["id"]))  # fmt: skip
    return out


@router.get("/me/opportunities", summary="Opportunities that fit your work, and those you watch")
def my_opportunities(request: Request) -> dict[str, Any]:
    engine, home = engine_of(request), request.app.state.home
    marks = store.interests(engine, _owner(request))
    hide = {ref for ref, state in marks.items() if state == "dismissed"}
    person_id = principal_of(request).person_id
    fits = fit.fitting(home, "person", person_id) if person_id else []
    items = _listing(request, fits, hide)
    watched = [ref for ref, state in marks.items() if state == "watching"]
    seen = {i["id"] for i in items}
    extra = [{"opportunity": ref, "band": None, "band_label": ""} for ref in watched
             if ref not in seen]  # fmt: skip
    items += _listing(request, extra, set())
    for item in items:
        item["watching"] = marks.get(item["id"]) == "watching"
    return envelope(items, linked=bool(person_id), dismissed=len(hide))


@router.get("/people/{person_id}/opportunities", summary="Open opportunities that fit a person")
def person_opportunities(request: Request, person_id: str) -> dict[str, Any]:
    fits = fit.fitting(request.app.state.home, "person", person_id)
    return envelope(_listing(request, fits, set()))


@router.get("/groups/{group_ref}/opportunities", summary="Open opportunities that fit a group")
def group_opportunities(request: Request, group_ref: str) -> dict[str, Any]:
    found = _group(request, group_ref)
    fits = fit.fitting(request.app.state.home, "group", found["id"])
    return envelope(_listing(request, fits, set()), group={"id": found["id"],
                                                           "name": found["name"]})  # fmt: skip
