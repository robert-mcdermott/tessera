"""The optional assistant's routes: installed only when ``[ai] enabled`` (see ai/web.py)."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any

import anyio.from_thread
import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from tessera.ai import log
from tessera.ai import summaries as lay
from tessera.ai.ask import Exchange, previous_turns
from tessera.ai.providers import ProviderError, make
from tessera.ai.tools import Fetch, Toolbox
from tessera.api.access import Level, principal_of
from tessera.api.common import config_of, engine_of, envelope
from tessera.core.clock import utc_now

router = APIRouter(prefix="/api/v1/ai", tags=["assistant"])

POLICIES = {
    ("GET", "/api/v1/ai/status"): Level.READ,
    ("GET", "/api/v1/ai/exchanges"): Level.ADMIN,
    ("GET", "/api/v1/ai/usage"): Level.ADMIN,
    ("POST", "/api/v1/ai/check"): Level.ADMIN,
    ("POST", "/api/v1/ai/ask"): Level.READ,
    # Who may review a given summary is decided in the handlers (the person, their curators,
    # the group's editors, admins).
    ("GET", "/api/v1/ai/summaries/{subject}"): Level.READ,
    ("POST", "/api/v1/ai/summaries/{subject}/draft"): Level.READ,
    ("POST", "/api/v1/ai/summaries/{summary_id}/approve"): Level.READ,
    ("POST", "/api/v1/ai/summaries/{summary_id}/reject"): Level.READ,
    ("POST", "/api/v1/ai/biosketch/{person_id}/draft"): Level.READ,  # the profile's keepers
}


def owner_of(request: Request) -> str:
    return principal_of(request).account_id or "operator"


def transport_of(request: Request) -> httpx.BaseTransport | None:
    """Tests stand a scripted model in for the provider here; otherwise the network."""
    return getattr(request.app.state, "ai_transport", None)


def may_use(request: Request) -> bool:
    principal = principal_of(request)
    roles = config_of(request).ai.roles
    return principal.is_admin or not roles or bool(principal.roles & set(roles))


def _may_spend(request: Request) -> None:
    """A draft is a paid call to the model, like a question: the same roles, and the same
    daily limit."""
    if not may_use(request):
        raise HTTPException(status_code=403, detail="Your role does not include the assistant.")
    limit = config_of(request).ai.daily_limit
    if limit and log.used_today(engine_of(request), owner_of(request)) >= limit:
        raise HTTPException(
            status_code=429, detail=f"You have used today's {limit:,} requests to the assistant."
        )


@router.get("/status", summary="Whether you may ask, and how many questions are left today")
def status(request: Request) -> dict[str, Any]:
    config = config_of(request).ai
    used = log.used_today(engine_of(request), owner_of(request))
    return envelope({
        "provider": config.provider, "model": config.model, "may_use": may_use(request),
        "daily_limit": config.daily_limit, "used_today": used,
        "left_today": None if not config.daily_limit else max(config.daily_limit - used, 0),
    })  # fmt: skip


@router.get("/exchanges", summary="The log: every exchange, newest first (admins)")
def exchanges(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    rows, total = log.recent(engine_of(request), limit, offset)
    return envelope(rows, total=total, limit=limit, offset=offset)


@router.get("/usage", summary="Exchanges and tokens by day, the last 30 days (admins)")
def usage(request: Request) -> dict[str, Any]:
    return envelope(log.usage_by_day(engine_of(request)))


@router.post("/check", summary="One round trip to the provider (admins)")
def check(request: Request) -> dict[str, Any]:
    config = config_of(request).ai
    started = utc_now()
    try:
        provider = make(config, transport=transport_of(request))
        try:
            reply = provider.complete("Reply with one word.", [{"role": "user", "text": "Ready?"}])
        finally:
            provider.close()
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    seconds = round((utc_now() - started).total_seconds(), 2)
    return envelope({"provider": config.provider, "model": config.model, "seconds": seconds,
                     "reply": reply.text[:200], "usage": reply.usage})  # fmt: skip


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    previous: str | None = Field(None, max_length=40, description="The exchange this follows")


def asker_fetch(request: Request) -> Fetch:
    """GET requests to this same app, in process, as the person asking: their session cookie
    and the host they used, so the access middleware and every route's policy and checks apply
    exactly as they do to their browser. Only GET is ever sent, so a tool cannot change anything.
    """
    app = request.app
    headers = {"host": request.headers.get("host", "127.0.0.1")}
    if request.headers.get("cookie"):
        headers["cookie"] = request.headers["cookie"]

    async def get(path: str, params: dict[str, Any]) -> tuple[int, Any]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://tessera") as client:
            response = await client.get(path, params=params, headers=headers)
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None

    def fetch(path: str, params: Mapping[str, Any]) -> tuple[int, Any]:
        if not path.startswith("/api/v1/") or "/ai/" in path:
            raise ValueError(f"The assistant's tools read the API only, not {path}.")
        return anyio.from_thread.run(get, path, dict(params))

    return fetch


@router.post("/ask", summary="Ask a question; the answer streams back as JSON lines")
def ask(request: Request, body: AskBody) -> StreamingResponse:
    if not may_use(request):
        raise HTTPException(status_code=403, detail="Your role does not include the assistant.")
    config = config_of(request)
    principal = principal_of(request)
    engine = engine_of(request)
    owner = owner_of(request)
    history, inherited, previous = previous_turns(engine, body.previous, owner)
    provider = make(config.ai, transport=transport_of(request))
    exchange = Exchange(
        engine=engine, config=config.ai, provider=provider,
        toolbox=Toolbox(asker_fetch(request), inherited), owner=owner,
        asked_by=principal.username or principal.name, organization=config.organization.name,
        question=body.question.strip(), history=history, previous=previous,
    )  # fmt: skip

    def lines() -> Iterator[str]:
        try:
            for event in exchange.events():
                yield json.dumps(event, ensure_ascii=False, default=str) + "\n"
        finally:
            provider.close()

    headers = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}  # stream through proxies
    return StreamingResponse(lines(), media_type="application/x-ndjson", headers=headers)


# --- plain-language summaries ---------------------------------------------------------------------


class Decision(BaseModel):
    text: str | None = Field(None, max_length=4000, description="The summary as edited, if edited")
    note: str | None = Field(None, max_length=1000)


def _subject(request: Request, ref: str) -> tuple[str, str, str, str]:
    try:
        with engine_of(request).connect() as conn:
            return lay.subject_of(conn, ref)
    except lay.SummaryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _reviewer(request: Request, kind: str, subject_id: str) -> str:
    principal = principal_of(request)
    if not lay.may_review(principal, engine_of(request), kind, subject_id):
        raise HTTPException(status_code=403, detail="Only this summary's reviewers may do that.")
    return principal.name  # shown beside an approved summary, the public site included


@router.get("/summaries/{subject}", summary="A person's or group's approved summary, and drafts")
def summary(request: Request, subject: str) -> dict[str, Any]:
    kind, subject_id, name, ref = _subject(request, subject)
    engine = engine_of(request)
    reviews = lay.may_review(principal_of(request), engine, kind, subject_id)
    with engine.connect() as conn:
        approved = lay.approved(conn, kind, [subject_id]).get(subject_id)
        out: dict[str, Any] = {
            "subject": {"kind": kind, "id": subject_id, "name": name, "ref": ref},
            "approved": lay.view(approved),
            "may_review": reviews,
        }
        if reviews:
            out["drafts"] = [lay.view(d) for d in lay.drafts(conn, kind, subject_id)]
            out["stale"] = lay.stale(conn, kind, subject_id)
            out["history"] = lay.history(conn, kind, subject_id)[:50]
    if approved and not reviews:
        out["approved"].pop("draft", None)  # what the model first wrote is for reviewers
    return envelope(out)


@router.post("/summaries/{subject}/draft", summary="Draft a summary for review (its reviewers)")
def draft_summary(request: Request, subject: str) -> dict[str, Any]:
    kind, subject_id, _, tool_ref = _subject(request, subject)
    actor = _reviewer(request, kind, subject_id)
    _may_spend(request)
    config = config_of(request)
    provider = make(config.ai, transport=transport_of(request))
    try:
        row = lay.draft(
            engine_of(request), config.ai, provider, Toolbox(asker_fetch(request)), ref=tool_ref,
            actor=actor, owner=owner_of(request), organization=config.organization.name,
        )  # fmt: skip
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except lay.SummaryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        provider.close()
    return envelope(lay.view(row))


def _decide(request: Request, summary_id: str, body: Decision, action: str) -> dict[str, Any]:
    engine = engine_of(request)
    with engine.connect() as conn:
        try:
            row = lay.get(conn, summary_id)
        except lay.SummaryError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    actor = _reviewer(request, row["subject_type"], row["subject_id"])
    try:
        with engine.begin() as conn:
            if action == "approve":
                row = lay.approve(conn, summary_id, actor=actor, text=body.text, note=body.note)
            else:
                row = lay.reject(conn, summary_id, actor=actor, note=body.note)
    except lay.SummaryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return envelope(lay.view(row))


@router.post("/summaries/{summary_id}/approve", summary="Approve a draft, as written or edited")
def approve_summary(request: Request, summary_id: str, body: Decision) -> dict[str, Any]:
    return _decide(request, summary_id, body, "approve")


@router.post("/summaries/{summary_id}/reject", summary="Reject a draft")
def reject_summary(request: Request, summary_id: str, body: Decision) -> dict[str, Any]:
    return _decide(request, summary_id, body, "reject")


# --- first drafts for a biosketch -------------------------------------------------------


class BiosketchDraft(BaseModel):
    part: str = Field(description="statement, or contribution")
    pmids: list[str] = Field(
        min_length=1, max_length=8, description="Their confirmed papers to draw on"
    )
    project: str = Field(
        "", max_length=1500, description="What the application is about (optional)"
    )


@router.post("/biosketch/{person_id}/draft",
             summary="A first draft for a biosketch, to edit (not saved)")  # fmt: skip
def biosketch_draft(request: Request, person_id: str, body: BiosketchDraft) -> dict[str, Any]:
    from tessera.ai import drafting
    from tessera.evidence.store import survivor_of
    from tessera.profiles import store as profiles
    from tessera.profiles.sections import ProfileError

    engine = engine_of(request)
    with engine.connect() as conn:
        person_id = survivor_of(conn, person_id)
    principal = principal_of(request)
    if profiles.capacity(principal, engine, person_id) is None:
        raise HTTPException(
            status_code=403, detail="Only those who keep this profile may draft for it."
        )
    _may_spend(request)
    config = config_of(request)
    provider = make(config.ai, transport=transport_of(request))
    try:
        out = drafting.draft(
            engine, config.ai, provider, Toolbox(asker_fetch(request)), person_id=person_id,
            part=body.part, pmids=body.pmids, project=body.project, owner=owner_of(request),
            actor=principal.name,
        )  # fmt: skip
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        provider.close()
    return envelope(out)
