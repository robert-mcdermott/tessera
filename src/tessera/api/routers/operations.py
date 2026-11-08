"""Operational status for administrators (PLT-12): runs, failures, durations, freshness, sizes,
the last backup, and what needs attention."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from tessera.api.common import config_of, engine_of, envelope
from tessera.ops.status import status

router = APIRouter(prefix="/api/v1", tags=["operations"])


@router.get(
    "/operations",
    summary="Runs over the last 30 days, sizes, the last backup, and issues (admins)",
)
def operations(request: Request) -> dict[str, Any]:
    return envelope(status(config_of(request), request.app.state.home, engine_of(request)))
