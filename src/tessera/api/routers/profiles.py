"""A person's profile: the sections they keep (PRF-01, PRF-02).

Everyone signed in reads every profile. The person, a curator for them, and an admin change it;
whoever may is decided per person here (the route policy only asks that one is signed in).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel, Field

from tessera.api.access import principal_of
from tessera.api.common import engine_of, envelope, index_of, lake_of, publication_summaries
from tessera.evidence.store import survivor_of
from tessera.profiles import store
from tessera.profiles.sections import SECTIONS, ProfileError

router = APIRouter(prefix="/api/v1/people", tags=["profiles"])

CAPACITY_LABELS = {"self": "themselves", "curator": "as curator", "admin": "as admin",
                   "import": "by import"}  # fmt: skip


class EntryIn(BaseModel):
    section: str = Field(description="The section: " + ", ".join(SECTIONS))
    fields: dict[str, Any] = Field(description="The section's fields (see the profile's sections)")


class OrderIn(BaseModel):
    section: str
    ids: list[str] = Field(max_length=200, description="Every entry of the section, in order")


def _fail(exc: ProfileError) -> HTTPException:
    text = str(exc)
    if text.startswith("No such"):
        return HTTPException(status_code=404, detail=text)
    if text.startswith("Only people in the directory"):
        return HTTPException(status_code=409, detail=text)
    return HTTPException(status_code=400, detail=text)


def _person(request: Request, person_id: str) -> str:
    with engine_of(request).connect() as conn:
        return survivor_of(conn, person_id)


def _keeper(request: Request, person_id: str) -> tuple[str, str]:
    """(actor, capacity) when the principal may keep this profile; 403 otherwise."""
    principal = principal_of(request)
    capacity = store.capacity(principal, engine_of(request), person_id)
    if capacity is None:
        raise HTTPException(
            status_code=403,
            detail="This profile is not in your scope: only the person, a curator for them, or "
            "an admin may change it.",
        )  # fmt: skip
    return principal.name, capacity


def _featured(request: Request, rows: list[dict[str, Any]], confirmed: set[str]
              ) -> list[dict[str, Any]]:  # fmt: skip
    pmids = [r["fields"]["pmid"] for r in rows]
    try:
        summaries = publication_summaries(index_of(request), lake_of(request), pmids)
    except Exception:  # the profile still answers without the search index
        summaries = {}
    out = []
    for r in rows:
        pmid = r["fields"]["pmid"]
        out.append({**r, "paper": summaries.get(pmid), "confirmed": pmid in confirmed})
    return out


@router.get("/{person_id}/profile", summary="A person's profile: the sections they keep")
def get_profile(request: Request, person_id: str) -> dict[str, Any]:
    person_id = _person(request, person_id)
    engine = engine_of(request)
    capacity = store.capacity(principal_of(request), engine, person_id)
    with engine.connect() as conn:
        found = store.entries(conn, person_id)
        confirmed = store.confirmed_papers(conn, person_id) if found.get("featured") else set()
    sections = []
    for spec in SECTIONS.values():
        rows = found.get(spec.id, [])
        if spec.id == "featured":
            rows = _featured(request, rows, confirmed)
            if capacity is None:  # a paper no longer confirmed is not shown to readers
                rows = [r for r in rows if r["confirmed"]]
        sections.append({**spec.to_json(), "entries": rows})
    return envelope({"person_id": person_id, "may_edit": capacity is not None,
                     "capacity": capacity, "sections": sections})  # fmt: skip


@router.post("/{person_id}/profile/entries", status_code=201,
             summary="Add an entry to a section of the profile")  # fmt: skip
def add_entry(request: Request, person_id: str, body: EntryIn) -> dict[str, Any]:
    person_id = _person(request, person_id)
    actor, capacity = _keeper(request, person_id)
    try:
        entry = store.add(engine_of(request), person_id, body.section, body.fields,
                          actor=actor, capacity=capacity)  # fmt: skip
    except ProfileError as exc:
        raise _fail(exc) from exc
    return envelope(entry)


@router.post("/{person_id}/profile/entries/{entry_id}", summary="Change an entry")
def change_entry(
    request: Request, person_id: str, entry_id: str,
    fields: Annotated[dict[str, Any], Body(embed=True)],
) -> dict[str, Any]:  # fmt: skip
    person_id = _person(request, person_id)
    actor, capacity = _keeper(request, person_id)
    try:
        entry = store.change(engine_of(request), person_id, entry_id, fields,
                             actor=actor, capacity=capacity)  # fmt: skip
    except ProfileError as exc:
        raise _fail(exc) from exc
    return envelope(entry)


@router.delete("/{person_id}/profile/entries/{entry_id}", summary="Remove an entry")
def remove_entry(request: Request, person_id: str, entry_id: str) -> dict[str, Any]:
    person_id = _person(request, person_id)
    actor, capacity = _keeper(request, person_id)
    try:
        store.remove(engine_of(request), person_id, entry_id, actor=actor, capacity=capacity)
    except ProfileError as exc:
        raise _fail(exc) from exc
    return envelope({"id": entry_id, "removed": True})


@router.post("/{person_id}/profile/order", summary="Put a section's entries in a new order")
def order_entries(request: Request, person_id: str, body: OrderIn) -> dict[str, Any]:
    person_id = _person(request, person_id)
    actor, capacity = _keeper(request, person_id)
    try:
        rows = store.reorder(engine_of(request), person_id, body.section, body.ids,
                             actor=actor, capacity=capacity)  # fmt: skip
    except ProfileError as exc:
        raise _fail(exc) from exc
    return envelope(rows)


@router.get("/{person_id}/profile/history", summary="Every change to the profile, newest first")
def profile_history(
    request: Request, person_id: str, limit: int = Query(100, ge=1, le=500)
) -> dict[str, Any]:
    person_id = _person(request, person_id)
    with engine_of(request).connect() as conn:
        rows = store.history(conn, person_id, limit)
    for row in rows:
        spec = SECTIONS.get(row["section"])
        row["section_label"] = spec.label if spec else row["section"]
        row["capacity_label"] = CAPACITY_LABELS.get(row["capacity"], row["capacity"])
    return envelope(rows)
