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
