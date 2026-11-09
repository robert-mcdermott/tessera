"""Health and metadata endpoints."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from tessera import TAGLINE, __version__
from tessera.config import Config
from tessera.connectors import CONNECTORS
from tessera.core.clock import isoformat, utc_now
from tessera.jobs import runs
from tessera.jobs.ingest import expected_days
from tessera.ledger import migrate

router = APIRouter(prefix="/api/v1", tags=["system"])


def _config(request: Request) -> Config:
    return request.app.state.config


@router.get("/health", summary="Liveness and readiness")
def health(request: Request) -> JSONResponse:
    """``200`` when the ledger and lake are usable, ``503`` otherwise, with per-check detail."""
    checks: dict[str, Any] = {}
    try:
        with request.app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["ledger"] = "ok" if migrate.is_current(request.app.state.engine) else "outdated"
    except Exception:  # report, don't raise: health must always answer
        checks["ledger"] = "unavailable"
    try:
        request.app.state.lake.scalar("SELECT 1")
        checks["lake"] = "ok"
    except Exception:
        checks["lake"] = "unavailable"
    healthy = all(value == "ok" for value in checks.values())
    body = {"status": "ok" if healthy else "degraded", "version": __version__, "checks": checks}
    return JSONResponse(body, status_code=200 if healthy else 503)


@router.get("/meta", summary="Product, branding, and organization details for the web app")
def meta(request: Request) -> dict[str, Any]:
    config = _config(request)
    signed_in = getattr(request.state, "principal", None) is not None
    return {
        "data": {
            "product": {
                "name": config.branding.product_name,
                "tagline": TAGLINE,
                "version": __version__,
                "theme": config.branding.theme,
                "accent_color": config.branding.accent_color,
                "logo": {
                    "light": "/branding/logo-light",
                    "dark": "/branding/logo-dark",
                }
                if config.branding.logo_light or config.branding.logo_dark
                else None,
                "show_powered_by": config.branding.show_powered_by,
                "footer_links": [
                    {"label": label, "url": url} for label, url in config.branding.footer_links
                ],
            },
            "organization": {
                "name": config.organization.name,
                "short_name": config.organization.short_name,
            },
            "mode": {
                "accounts": config.server.accounts,
                "operator": None if config.server.accounts else config.server.operator,
                # The sign-in page's single sign-on button, when it is set up.
                "sso": {"label": config.auth.oidc.label}
                if config.server.accounts and config.auth.oidc.enabled
                else None,
            },
            # Before sign-in, only what the sign-in page shows (name, branding).
            "sources": _freshness(request, config) if signed_in else [],
            "vocabulary": config.vocabulary.to_json() if signed_in else None,
            # Where the public site is served ([publish] base_url), for embed codes.
            "public_site": (config.publish.base_url.rstrip("/") or None) if signed_in else None,
            # Front-end modules to load, for optional features the configuration turns on.
            "extensions": list(getattr(request.app.state, "extensions", [])) if signed_in else [],
        }
    }


def _freshness(request: Request, config: Config) -> list[dict[str, Any]]:
    """Each source's last successful fetch, and whether it is older than expected."""
    try:
        last = runs.last_success(request.app.state.engine)
    except Exception:  # meta must answer even when the ledger is unavailable
        last = {}
    now = utc_now()
    out = []
    for name, connector in CONNECTORS.items():
        finished = last.get(name)
        enabled = getattr(config.sources, name).enabled
        out.append(
            {
                "name": name,
                "label": connector.label,
                "enabled": enabled,
                "last_success_at": isoformat(finished) if finished else None,
                "stale": bool(
                    enabled
                    and (finished is None or now - finished > timedelta(days=expected_days(name)))
                ),
            }
        )
    return out
