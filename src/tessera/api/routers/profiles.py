"""A person's profile: the sections they keep (PRF-01, PRF-02).

Everyone signed in reads every profile. The person, a curator for them, and an admin change it;
whoever may is decided per person here (the route policy only asks that one is signed in).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from tessera.api.access import principal_of
from tessera.api.common import (
    config_of,
    engine_of,
    envelope,
    index_of,
    lake_of,
    publication_summaries,
)
from tessera.evidence.store import survivor_of
from tessera.ledger.tables import person
from tessera.profiles import store
from tessera.profiles.sections import SECTIONS, ProfileError

router = APIRouter(prefix="/api/v1/people", tags=["profiles"])
me_router = APIRouter(prefix="/api/v1/me", tags=["profiles"])

CAPACITY_LABELS = {
    "self": "themselves",
    "delegate": "acting for them",
    "curator": "as curator",
    "admin": "as admin",
    "import": "by import",
}


# History entries that are not a section.
SPECIAL = {"delegates": "Who keeps the profile", "public": "Public page", "documents": "CVs"}


class EntryIn(BaseModel):
    section: str = Field(description="The section: " + ", ".join(SECTIONS))
    fields: dict[str, Any] = Field(description="The section's fields (see the profile's sections)")


class DelegateIn(BaseModel):
    username: str = Field(min_length=1, max_length=64, description="The account to name")


class PublicChoice(BaseModel):
    public: bool | None = Field(None, description="Whether the person has a public page")
    hidden_sections: list[str] | None = Field(
        None, max_length=20, description="Sections the public page leaves off"
    )


class EntryPublic(BaseModel):
    hidden: bool = Field(description="Leave this entry off the public page")


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
            detail="This profile is not in your scope: only the person, someone they named, a "
            "curator for them, or an admin may change it.",
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
        keepers = store.delegates(conn, person_id)
        hidden = store.hidden_sections(conn, person_id)
        is_public = bool(
            conn.execute(select(person.c.public).where(person.c.id == person_id)).scalar()
        )
        confirmed = store.confirmed_papers(conn, person_id) if found.get("featured") else set()
    sections = []
    for spec in SECTIONS.values():
        rows = found.get(spec.id, [])
        if spec.id == "featured":
            rows = _featured(request, rows, confirmed)
            if capacity is None:  # a paper no longer confirmed is not shown to readers
                rows = [r for r in rows if r["confirmed"]]
        sections.append({**spec.to_json(), "entries": rows})
    # Everyone sees who keeps the profile for the person; its keepers also see the accounts.
    named = [
        {"name": d["name"]}
        | ({"account_id": d["account_id"], "username": d["username"]} if capacity else {})
        for d in keepers
        if d["status"] == "active"
    ]
    return envelope({"person_id": person_id, "may_edit": capacity is not None,
                     "may_name": capacity in store.NAMERS, "delegates": named,
                     "accounts": config_of(request).server.accounts,
                     "public": is_public, "hidden_sections": hidden,
                     "public_sections": [{"id": s, "label": store.public_label(s)}
                                         for s in store.PUBLIC_SECTIONS],
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
        row["section_label"] = spec.label if spec else SPECIAL.get(row["section"], row["section"])
        row["capacity_label"] = CAPACITY_LABELS.get(row["capacity"], row["capacity"])
    return envelope(rows)


@router.post("/{person_id}/profile/delegates", status_code=201,
             summary="Name an account that keeps this profile for the person")  # fmt: skip
def name_delegate(request: Request, person_id: str, body: DelegateIn) -> dict[str, Any]:
    person_id = _person(request, person_id)
    actor, capacity = _keeper(request, person_id)
    try:
        found = store.name_delegate(engine_of(request), person_id, body.username,
                                    actor=actor, capacity=capacity)  # fmt: skip
    except ProfileError as exc:
        raise _namer_fail(exc) from exc
    return envelope(found)


@router.delete("/{person_id}/profile/delegates/{account_id}",
               summary="Stop an account keeping this profile")  # fmt: skip
def remove_delegate(request: Request, person_id: str, account_id: str) -> dict[str, Any]:
    person_id = _person(request, person_id)
    actor, capacity = _keeper(request, person_id)
    try:
        store.remove_delegate(engine_of(request), person_id, account_id, actor=actor,
                              capacity=capacity)  # fmt: skip
    except ProfileError as exc:
        raise _namer_fail(exc) from exc
    return envelope({"account_id": account_id, "removed": True})


def _namer_fail(exc: ProfileError) -> HTTPException:
    if str(exc).startswith("Only the person"):  # someone acting for them cannot name others
        return HTTPException(status_code=403, detail=f"Outside your scope: {exc}")
    return _fail(exc)


@me_router.get("/profiles", summary="The profiles you keep: your own, and those you act for")
def my_profiles(request: Request) -> dict[str, Any]:
    with engine_of(request).connect() as conn:
        return envelope(store.kept_by(conn, principal_of(request)))


@router.post("/{person_id}/profile/public",
             summary="Turn the public page on or off; choose its sections")  # fmt: skip
def choose_public(request: Request, person_id: str, body: PublicChoice) -> dict[str, Any]:
    person_id = _person(request, person_id)
    actor, capacity = _keeper(request, person_id)
    try:
        out = store.set_public(engine_of(request), person_id, actor=actor, capacity=capacity,
                               public=body.public, hidden=body.hidden_sections)  # fmt: skip
    except ProfileError as exc:
        raise _fail(exc) from exc
    return envelope(out)


@router.post("/{person_id}/profile/entries/{entry_id}/public",
             summary="Leave one entry off the public page, or put it back")  # fmt: skip
def entry_public(request: Request, person_id: str, entry_id: str, body: EntryPublic
                 ) -> dict[str, Any]:  # fmt: skip
    person_id = _person(request, person_id)
    actor, capacity = _keeper(request, person_id)
    try:
        entry = store.set_hidden(engine_of(request), person_id, entry_id, body.hidden,
                                 actor=actor, capacity=capacity)  # fmt: skip
    except ProfileError as exc:
        raise _fail(exc) from exc
    return envelope(entry)


PREVIEW_CSS = "/api/v1/me/public-page.css"


@router.get("/{person_id}/profile/public/preview", response_class=HTMLResponse,
            summary="The public page as publishing would build it now")  # fmt: skip
def preview_public(request: Request, person_id: str) -> HTMLResponse:
    import re

    from tessera.publish import pages as site_pages
    from tessera.publish.projection import project

    person_id = _person(request, person_id)
    _keeper(request, person_id)
    config = config_of(request)
    site = project(config, request.app.state.home, engine_of(request), only_person=person_id)
    if not site.people:
        raise HTTPException(status_code=409, detail="Only people in the directory have profiles.")
    html = site_pages.person_page(site, config.publish.base_url, site.people[0])
    # The page's own stylesheet, from here; its links to other public pages work once published.
    html = re.sub(r'href="[^"]*assets/site\.css"', f'href="{PREVIEW_CSS}"', html)
    html = html.replace("<title>", "<title>Preview: ", 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@me_router.get("/public-page.css", summary="The public pages' stylesheet, for previews")
def preview_stylesheet(request: Request) -> Response:
    from tessera.publish.style import stylesheet

    return Response(stylesheet(config_of(request)), media_type="text/css")


# --- CVs ------------------------------------------------------------------------------------------


class DocumentIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field("cv", description="cv, or nih_biosketch")
    copy_of: str | None = Field(None, description="Start from a copy of this CV or biosketch")


class DocumentChange(BaseModel):
    name: str | None = Field(None, max_length=200)
    spec: dict[str, Any] | None = None


def _cv_context(request: Request, person_id: str, doc: dict[str, Any]) -> dict[str, Any]:
    from datetime import date

    from tessera.profiles import cv

    config = config_of(request)
    try:
        index = index_of(request)
    except Exception:  # the CV still builds without the search index
        index = None
    with engine_of(request).connect() as conn:
        available = cv.catalogue(
            conn, config, request.app.state.home, index, person_id, date.today()
        )
        head = cv.header(conn, config, person_id)
    options = {
        sid: [
            {"id": str(r["id"]), "label": _option_label(sid, r, config.organization.name)}
            for r in rows
        ]
        for sid, rows in available.items()
    }
    if doc["kind"] == "nih_biosketch":
        from tessera.profiles import biosketch

        built = biosketch.build(doc["spec"], available, head)
        return {
            "document": {k: doc[k] for k in ("id", "kind", "name", "spec", "form", "updated_at",
                                             "updated_by")},
            "header": head,
            "options": {k: options[k] for k in ("education", "position", "honor", "publications")},
            "sections": built,
            "checks": biosketch.checks(doc["spec"], built, available),
            "sciencv": biosketch.sciencv(built),
            "form_label": biosketch.FORM_LABEL,
            "limits": biosketch.LIMITS,
        }  # fmt: skip
    return {
        "document": {k: doc[k] for k in ("id", "kind", "name", "spec", "form", "updated_at",
                                         "updated_by")},
        "header": head,
        "sections_available": [{"id": k, "label": v} for k, v in cv.SECTIONS.items()],
        "options": options,
        "sections": cv.build(doc["spec"], available),
    }  # fmt: skip


def _option_label(section: str, row: dict[str, Any], organization: str = "") -> str:
    """How an entry is named in the editor's lists: enough to tell two alike apart, such as one
    title held in two groups (their own organization's name left off, as it goes without
    saying)."""
    from tessera.profiles import format as fmt

    if section == "publications":
        return f"{row.get('year') or ''} · {row['title']}"
    if section in ("grants", "studies"):
        return f"{row['main']} ({row['id']})"
    if section == "overview":
        return row["fields"]["text"][:80]
    title, detail, dates = fmt.lines(section, row["fields"])
    if organization and detail != organization:
        detail = detail.removesuffix(f", {organization}")
    return " · ".join(x for x in (title, detail, dates) if x)


@router.get(
    "/{person_id}/cvs", summary="A person's CVs and biosketches (those who keep the profile)"
)
def list_documents(request: Request, person_id: str) -> dict[str, Any]:
    from tessera.profiles import cv

    person_id = _person(request, person_id)
    _keeper(request, person_id)
    with engine_of(request).connect() as conn:
        return envelope(cv.documents(conn, person_id))


@router.post("/{person_id}/cvs", status_code=201, summary="Start a CV, or a copy of one")
def create_document(request: Request, person_id: str, body: DocumentIn) -> dict[str, Any]:
    from tessera.profiles import cv

    person_id = _person(request, person_id)
    actor, capacity = _keeper(request, person_id)
    if body.kind not in ("cv", "nih_biosketch"):
        raise HTTPException(status_code=400, detail="A document is a cv or an nih_biosketch.")
    try:
        spec, kind, form = None, body.kind, None
        if body.copy_of:
            with engine_of(request).connect() as conn:
                source = cv.get(conn, person_id, body.copy_of)
            spec, kind, form = source["spec"], source["kind"], source["form"]
        elif kind == "nih_biosketch":
            from datetime import date

            from tessera.profiles import biosketch

            config = config_of(request)
            with engine_of(request).connect() as conn:
                available = cv.catalogue(conn, config, request.app.state.home, None, person_id,
                                         date.today())  # fmt: skip
            spec, form = biosketch.default_spec(available, config.organization.name), biosketch.FORM
        doc = cv.create(engine_of(request), person_id, name=body.name, kind=kind, spec=spec,
                        form=form, actor=actor, capacity=capacity)  # fmt: skip
    except ProfileError as exc:
        raise _fail(exc) from exc
    return envelope(_cv_context(request, person_id, doc))


@router.get("/{person_id}/cvs/{doc_id}", summary="A CV, built from the profile and records now")
def get_document(request: Request, person_id: str, doc_id: str) -> dict[str, Any]:
    from tessera.profiles import cv

    person_id = _person(request, person_id)
    _keeper(request, person_id)
    try:
        with engine_of(request).connect() as conn:
            doc = cv.get(conn, person_id, doc_id)
    except ProfileError as exc:
        raise _fail(exc) from exc
    return envelope(_cv_context(request, person_id, doc))


@router.post("/{person_id}/cvs/{doc_id}", summary="Rename a CV, or change what it includes")
def change_document(request: Request, person_id: str, doc_id: str, body: DocumentChange
                    ) -> dict[str, Any]:  # fmt: skip
    from tessera.profiles import cv

    person_id = _person(request, person_id)
    actor, capacity = _keeper(request, person_id)
    try:
        doc = cv.change(engine_of(request), person_id, doc_id, name=body.name, spec=body.spec,
                        actor=actor, capacity=capacity)  # fmt: skip
    except ProfileError as exc:
        raise _fail(exc) from exc
    return envelope(_cv_context(request, person_id, doc))


@router.delete("/{person_id}/cvs/{doc_id}", summary="Delete a CV")
def delete_document(request: Request, person_id: str, doc_id: str) -> dict[str, Any]:
    from tessera.profiles import cv

    person_id = _person(request, person_id)
    actor, capacity = _keeper(request, person_id)
    try:
        cv.remove(engine_of(request), person_id, doc_id, actor=actor, capacity=capacity)
    except ProfileError as exc:
        raise _fail(exc) from exc
    return envelope({"id": doc_id, "removed": True})


@router.get("/{person_id}/cvs/{doc_id}/markdown", summary="A CV as Markdown, to download")
def document_markdown(request: Request, person_id: str, doc_id: str) -> Response:
    import re
    from datetime import date

    from tessera.profiles import cv

    person_id = _person(request, person_id)
    _keeper(request, person_id)
    context = get_document(request, person_id, doc_id)["data"]
    if context["document"]["kind"] == "nih_biosketch":
        from tessera.profiles import biosketch

        text = biosketch.markdown(context["document"]["name"], context["sections"])
    else:
        text = cv.markdown(context["document"]["name"], context["header"], context["sections"],
                           date.today())  # fmt: skip
    stem = re.sub(
        r"[^A-Za-z0-9]+", "-", f"{context['header']['name']} {context['document']['name']}"
    )
    return Response(text, media_type="text/markdown; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{stem.strip("-")}.md"'})  # fmt: skip
