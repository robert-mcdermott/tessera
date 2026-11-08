"""Teams for an opportunity (INT-10): who is on it, what it covers, and its brief."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from tessera.api.access import principal_of
from tessera.api.common import config_of, engine_of, envelope, index_of, lake_of
from tessera.evidence.store import display_names
from tessera.opportunities import catalog, fit
from tessera.teams import brief, coverage, store
from tessera.teams.store import TeamError, TeamNotFound

router = APIRouter(prefix="/api/v1", tags=["teams"])


def _fail(exc: TeamError) -> HTTPException:
    return HTTPException(status_code=404 if isinstance(exc, TeamNotFound) else 400,
                         detail=str(exc))  # fmt: skip


def _summary(found: dict[str, Any], principal: Any) -> dict[str, Any]:
    return {
        "id": found["id"], "name": found["name"], "purpose": found["purpose"],
        "opportunity": found["opportunity"], "owner_name": found["owner_name"],
        "members": len(found["members"]), "concepts": len(found["concepts"] or []),
        "updated_at": found["updated_at"], "mine": store.owns(found, principal),
    }  # fmt: skip


def _detail(request: Request, found: dict[str, Any]) -> dict[str, Any]:
    principal = principal_of(request)
    engine, home = engine_of(request), request.app.state.home
    result = coverage.analyze(home, engine, found)
    opportunity = None
    fitting: list[dict[str, Any]] = []
    if found["opportunity"]:
        view = catalog.get(engine, lake_of(request), found["opportunity"])
        if view:
            keys = (
                "id",
                "title",
                "number",
                "sponsor",
                "status",
                "status_label",
                "closes",
                "expected_post",
            )
            opportunity = {k: view[k] for k in keys}
            fits = fit.fits_of(home, view["id"]) or {"people": []}
            members = {m["person_id"] for m in found["members"]}
            fitting = [p for p in fits["people"] if p["id"] not in members][:8]
            names = display_names(engine, [p["id"] for p in fitting])
            fitting = [{**p, "name": names.get(p["id"], p["id"])} for p in fitting]
    shared = display_names(engine, found["shared_with"] or [])
    return {
        **{k: found[k] for k in ("id", "name", "purpose", "notes", "opportunity", "owner_name",
                                 "created_at", "updated_at")},
        "concepts": found["concepts"] or [],
        "members": [{"id": m["person_id"], "name": m["name"], "role": m["role"],
                     "role_label": store.ROLE_LABELS.get(m["role"], m["role"]), "note": m["note"],
                     "added_by": m["added_by"], "added_at": m["added_at"]}
                    for m in found["members"]],
        "shared_with": [{"id": p, "name": shared.get(p, p)} for p in found["shared_with"] or []],
        "can_share": store.owns(found, principal),
        "coverage": result,
        "opportunity_view": opportunity,
        "people_who_fit": fitting,
        "roles": [{"value": k, "label": v} for k, v in store.ROLE_LABELS.items()],
    }  # fmt: skip


def _get(request: Request, team_id: str) -> dict[str, Any]:
    try:
        return store.get_visible(engine_of(request), team_id, principal_of(request))
    except TeamError as exc:
        raise _fail(exc) from exc


@router.get("/teams", summary="Teams you own, are on, or that are shared with you")
def list_teams(request: Request) -> dict[str, Any]:
    principal = principal_of(request)
    return envelope([_summary(t, principal) for t in store.list_for(engine_of(request), principal)])


@router.post("/teams", summary="Start a team, for an opportunity or for topics you choose")
def start_team(request: Request, body: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    engine, home = engine_of(request), request.app.state.home
    fields = dict(body)
    ref = fields.get("opportunity")
    if ref:
        view = catalog.get(engine, lake_of(request), str(ref))
        if view is None:
            raise HTTPException(status_code=404, detail="No such opportunity.")
        fields["opportunity"] = view["id"]
        fields.setdefault("name", f"Team for {view['title']}"[:200])
        if "concepts" not in fields:
            read = fit.fits_of(home, view["id"])
            if read is None:
                from tessera.analytics.concepts import profile_path
                from tessera.analytics.experts import ConceptReader, _exists
                from tessera.analytics.vocabulary import vocabulary_path

                if _exists(profile_path(home)):
                    reader = ConceptReader(lake_of(request), profile_path(home),
                                           vocabulary_path(home))  # fmt: skip
                    read = fit.read_one(reader, index_of(request), view)
            fields["concepts"] = (read or {}).get("concepts", [])
    try:
        team_id = store.create(engine, principal_of(request), fields)
    except TeamError as exc:
        raise _fail(exc) from exc
    return envelope(_detail(request, _get(request, team_id)))


@router.get("/teams/{team_id}", summary="A team: members, topics covered and missing, pairings")
def get_team(request: Request, team_id: str) -> dict[str, Any]:
    return envelope(_detail(request, _get(request, team_id)))


@router.post("/teams/{team_id}", summary="Change a team's name, purpose, notes, topics, or sharing")
def change_team(
    request: Request, team_id: str, body: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    try:
        store.change(engine_of(request), team_id, body, principal_of(request))
    except TeamError as exc:
        raise _fail(exc) from exc
    return envelope(_detail(request, _get(request, team_id)))


@router.delete("/teams/{team_id}", summary="Delete a team (its owner, or an admin)")
def delete_team(request: Request, team_id: str) -> dict[str, Any]:
    try:
        store.remove(engine_of(request), team_id, principal_of(request))
    except TeamError as exc:
        raise _fail(exc) from exc
    return envelope({"id": team_id, "deleted": True})


@router.post("/teams/{team_id}/members", summary="Add someone to a team, or change their role")
def add_member(
    request: Request,
    team_id: str,
    person_id: Annotated[str, Body(embed=True)],
    role: Annotated[str, Body(embed=True)] = "collaborator",
    note: Annotated[str | None, Body(embed=True)] = None,
) -> dict[str, Any]:
    try:
        store.set_member(engine_of(request), team_id, person_id, principal_of(request), role, note)
    except TeamError as exc:
        raise _fail(exc) from exc
    return envelope(_detail(request, _get(request, team_id)))


@router.delete("/teams/{team_id}/members/{person_id}", summary="Take someone off a team")
def remove_member(request: Request, team_id: str, person_id: str) -> dict[str, Any]:
    try:
        store.remove_member(engine_of(request), team_id, person_id, principal_of(request))
    except TeamError as exc:
        raise _fail(exc) from exc
    return envelope(_detail(request, _get(request, team_id)))


@router.get("/teams/{team_id}/brief", summary="The team brief: JSON, Markdown, or CSV")
def team_brief(
    request: Request,
    team_id: str,
    format: str = Query("json", pattern="^(json|markdown|csv)$"),
) -> Any:
    found = _get(request, team_id)
    built = brief.build(config_of(request), request.app.state.home, engine_of(request), found,
                        date.today())  # fmt: skip
    if format == "json":
        return envelope(built)
    stem = "".join(c if c.isalnum() else "-" for c in found["name"].lower()).strip("-")[:60]
    stem = stem or "team"
    if format == "markdown":
        text, kind, name = brief.markdown(built), "text/markdown", f"{stem}-brief.md"
    else:
        text, kind, name = brief.members_csv(built), "text/csv", f"{stem}-members.csv"
    return PlainTextResponse(
        text,
        media_type=f"{kind}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
