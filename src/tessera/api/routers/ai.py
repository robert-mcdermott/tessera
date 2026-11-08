"""The optional assistant's routes: installed only when ``[ai] enabled`` (see ai/web.py)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from tessera.ai import log
from tessera.ai.providers import ProviderError, make
from tessera.api.access import Level, principal_of
from tessera.api.common import config_of, engine_of, envelope
from tessera.core.clock import utc_now

router = APIRouter(prefix="/api/v1/ai", tags=["assistant"])

POLICIES = {
    ("GET", "/api/v1/ai/status"): Level.READ,
    ("GET", "/api/v1/ai/exchanges"): Level.ADMIN,
    ("GET", "/api/v1/ai/usage"): Level.ADMIN,
    ("POST", "/api/v1/ai/check"): Level.ADMIN,
}


def owner_of(request: Request) -> str:
    return principal_of(request).account_id or "operator"


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
        provider = make(config)
        try:
            reply = provider.complete("Reply with one word.", [{"role": "user", "text": "Ready?"}])
        finally:
            provider.close()
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    seconds = round((utc_now() - started).total_seconds(), 2)
    return envelope({"provider": config.provider, "model": config.model, "seconds": seconds,
                     "reply": reply.text[:200], "usage": reply.usage})  # fmt: skip
