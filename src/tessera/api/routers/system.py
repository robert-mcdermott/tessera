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
    return {
        "data": {
            "product": {
                "name": config.branding.product_name,
                "tagline": TAGLINE,
                "version": __version__,
                "accent_color": config.branding.accent_color,
                "show_powered_by": config.branding.show_powered_by,
            },
            "organization": {
                "name": config.organization.name,
                "short_name": config.organization.short_name,
            },
            "mode": {"accounts": False, "operator": config.server.operator},
            "sources": _freshness(request, config),
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
