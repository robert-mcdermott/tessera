"""The authorship model: learned weights, their evaluation, and auto-accept (CUR-05, CUR-10)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from tessera.api.access import principal_of
from tessera.api.common import config_of, engine_of, envelope
from tessera.evidence import auto_accept
from tessera.evidence.model import (
    EVALUATION_SETTING,
    ModelError,
    active_model,
    evaluate,
    examples_from_ledger,
    read_setting,
    release,
    revert,
)

router = APIRouter(prefix="/api/v1/evidence", tags=["evidence"])


def _overview(request: Request) -> dict[str, Any]:
    engine = engine_of(request)
    model = active_model(engine)
    return {
        "model": {**model.to_json(), "hand_set": model.is_base},
        "decisions": len(examples_from_ledger(engine)),
        "evaluation": read_setting(engine, EVALUATION_SETTING),
        "auto_accept": auto_accept.status(engine),
        "batches": auto_accept.batches(engine),
    }


@router.get("", summary="The active model, its latest evaluation, and auto-accept")
def overview(request: Request) -> dict[str, Any]:
    return envelope(_overview(request))


@router.post("/evaluate", summary="Fit learned weights and compare on held-out people (admins)")
def run_evaluation(request: Request) -> dict[str, Any]:
    evaluate(engine_of(request), config_of(request), actor=principal_of(request).name)
    return envelope(_overview(request))


@router.post("/release", summary="Make the evaluated learned model active, if it was better")
def release_model(request: Request) -> dict[str, Any]:
    try:
        release(engine_of(request), principal_of(request).name)
    except ModelError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return envelope(_overview(request))


@router.post("/revert", summary="Go back to the hand-set weights")
def revert_model(request: Request) -> dict[str, Any]:
    revert(engine_of(request), principal_of(request).name)
    return envelope(_overview(request))


class AutoAcceptChange(BaseModel):
    enabled: bool
    threshold: float | None = Field(None, gt=0, le=1)


@router.post("/auto-accept", summary="Switch auto-accept on at a measured threshold, or off")
def change_auto_accept(request: Request, body: AutoAcceptChange) -> dict[str, Any]:
    engine, actor = engine_of(request), principal_of(request).name
    try:
        if body.enabled:
            if body.threshold is None:
                raise HTTPException(status_code=400, detail="Give the threshold to accept at.")
            auto_accept.enable(engine, body.threshold, actor)
        else:
            auto_accept.disable(engine, actor)
    except auto_accept.AutoAcceptError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return envelope(_overview(request))


@router.post("/auto-accept/batches/{batch_id}/undo", summary="Undo one automatic batch")
def undo_batch(request: Request, batch_id: str) -> dict[str, Any]:
    try:
        count = auto_accept.undo_batch(engine_of(request), batch_id, principal_of(request).name)
    except auto_accept.AutoAcceptError as exc:
        status = 404 if str(exc).startswith("No batch") else 409
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return envelope({**_overview(request), "restored": count})
