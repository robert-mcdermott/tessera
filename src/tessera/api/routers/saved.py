"""Saved searches (DIS-10): each person's own, with what is new since they last looked."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from tessera.api.access import principal_of
from tessera.api.common import engine_of, envelope, index_of
from tessera.core.clock import isoformat
from tessera.core.errors import TesseraError
from tessera.search import saved
from tessera.search.query import SearchUnavailable

router = APIRouter(prefix="/api/v1/saved-searches", tags=["saved searches"])


def _owner(request: Request) -> str:
    principal = principal_of(request)
    return principal.account_id or "operator"


def _fail(exc: TesseraError) -> HTTPException:
    message = str(exc)
    if isinstance(exc, SearchUnavailable):
        return HTTPException(status_code=503, detail=message)
    return HTTPException(status_code=404 if message.startswith("No saved") else 400, detail=message)


def _view(row: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    out = {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "q": row["q"],
        "filters": row["filters"] or {},
        "seen_at": isoformat(row["seen_at"]),
        "created_at": isoformat(row["created_at"]),
    }
    if state is not None:
        out |= {"total": state["total"], "new": len(state["new"])}
    return out


class SavedIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field("everything", description="person, publication, grant, study, or everything")
    q: str = Field("", max_length=500)
    filters: dict[str, list[str]] = Field(default_factory=dict)


class SavedChange(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.get("", summary="Your saved searches, with how many matches are new")
def list_saved(request: Request) -> dict[str, Any]:
    index = index_of(request)
    with engine_of(request).connect() as conn:
        rows = saved.list_for(conn, _owner(request))
    try:
        return envelope([_view(r, saved.check(index, r)) for r in rows])
    except TesseraError as exc:
        raise _fail(exc) from exc


@router.post("", summary="Save a search; what it finds now counts as seen")
def create_saved(request: Request, body: SavedIn) -> dict[str, Any]:
    index = index_of(request)
    try:
        with engine_of(request).begin() as conn:
            search_id = saved.create(
                conn, index, owner=_owner(request), name=body.name, kind=body.kind, q=body.q,
                filters=body.filters,
            )  # fmt: skip
            row = saved.get(conn, search_id, _owner(request))
        return envelope(_view(row, saved.check(index, row)))
    except TesseraError as exc:
        raise _fail(exc) from exc


@router.get("/{search_id}", summary="A saved search with its new matches")
def get_saved(request: Request, search_id: str) -> dict[str, Any]:
    index = index_of(request)
    try:
        with engine_of(request).connect() as conn:
            row = saved.get(conn, search_id, _owner(request))
        state = saved.check(index, row)
        summaries = index.summaries(state["new"][:200])
    except TesseraError as exc:
        raise _fail(exc) from exc
    return envelope(
        {
            **_view(row, state),
            "new_refs": state["new"],
            "new_items": [{"ref": r, "summary": summaries.get(r)} for r in state["new"][:200]],
        }
    )


@router.post("/{search_id}", summary="Rename a saved search")
def rename_saved(request: Request, search_id: str, body: SavedChange) -> dict[str, Any]:
    try:
        with engine_of(request).begin() as conn:
            saved.rename(conn, search_id, _owner(request), body.name)
            row = saved.get(conn, search_id, _owner(request))
    except TesseraError as exc:
        raise _fail(exc) from exc
    return envelope(_view(row))


@router.post("/{search_id}/seen", summary="Count every current match as seen")
def mark_seen(request: Request, search_id: str) -> dict[str, Any]:
    try:
        with engine_of(request).begin() as conn:
            saved.mark_seen(conn, index_of(request), search_id, _owner(request))
    except TesseraError as exc:
        raise _fail(exc) from exc
    return envelope({"seen": search_id})


@router.delete("/{search_id}", summary="Delete a saved search")
def delete_saved(request: Request, search_id: str) -> dict[str, Any]:
    try:
        with engine_of(request).begin() as conn:
            saved.remove(conn, search_id, _owner(request))
    except TesseraError as exc:
        raise _fail(exc) from exc
    return envelope({"deleted": search_id})
