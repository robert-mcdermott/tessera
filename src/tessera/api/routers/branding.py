"""Admin → Branding: the organization's look, checked as it changes and saved to the
configuration file (PLT-13). See ``tessera.brandkit``."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from tessera import brandkit
from tessera.api.common import config_of, config_stamp, envelope
from tessera.branding import COLOR_NAMES, DAY, NIGHT
from tessera.config import PALETTES

router = APIRouter(prefix="/api/v1/admin/branding", tags=["branding"])


def _proposal_json(proposal: brandkit.Proposal) -> dict[str, Any]:
    out = asdict(proposal)
    out["footer_links"] = [{"label": label, "url": url} for label, url in proposal.footer_links]
    return out


def _state(request: Request) -> dict[str, Any]:
    config = config_of(request)
    proposal = brandkit.current(config)
    day, night = brandkit.resolved(proposal)
    path = config.path
    writable = bool(path) and os.access(path, os.W_OK) and os.access(path.parent, os.W_OK)
    return {
        "settings": _proposal_json(proposal),
        "colors": {"day": asdict(day), "night": asdict(night)},
        "defaults": {"day": asdict(DAY), "night": asdict(NIGHT)},
        "color_names": list(COLOR_NAMES),
        "palettes": list(PALETTES),
        "contrast": brandkit.contrast_table(proposal),
        "toml": brandkit.section(proposal),
        "file": {"name": path.name if path else None, "writable": writable},
    }


def _refused(exc: brandkit.BrandingRefused) -> JSONResponse:
    return JSONResponse(
        {
            "type": "about:blank",
            "title": "Not saved",
            "status": 422,
            "detail": exc.problems[0]
            if len(exc.problems) == 1
            else "This branding cannot be saved.",
            "problems": exc.problems,
        },
        status_code=422,
        media_type="application/problem+json",
    )


@router.get("", summary="The organization's branding as configured, with its contrast checks")
def get_branding(request: Request) -> dict[str, Any]:
    return envelope(_state(request))


async def _proposal(request: Request) -> brandkit.Proposal:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise brandkit.BrandingRefused(["Send the branding as a JSON object."]) from exc
    if not isinstance(body, dict):
        raise brandkit.BrandingRefused(["Send the branding as a JSON object."])
    return brandkit.proposal_from(body)


@router.post("/check", summary="Check proposed branding: contrast by day and night, and problems")
async def check_branding(request: Request) -> Any:
    try:
        proposal = await _proposal(request)
    except brandkit.BrandingRefused as exc:
        return _refused(exc)
    config = config_of(request)
    base = config.path.parent if config.path else Path.cwd()
    day, night = brandkit.resolved(proposal)
    return envelope(
        {
            "colors": {"day": asdict(day), "night": asdict(night)},
            "contrast": brandkit.contrast_table(proposal),
            "problems": brandkit.problems_of(proposal, base),
            "toml": brandkit.section(proposal),
        }
    )


@router.post("", summary="Save branding to the configuration file (refused below WCAG AA)")
async def save_branding(request: Request) -> Any:
    config = config_of(request)
    try:
        saved = brandkit.save(config, await _proposal(request))
    except brandkit.BrandingRefused as exc:
        return _refused(exc)
    # Only the branding takes effect now; anything else changed in the file waits for a restart.
    request.app.state.config = replace(config, branding=saved.branding)
    request.app.state.config_stamp = config_stamp(config)  # other processes follow the file
    return envelope(_state(request))


@router.post("/images/{image}", summary="Upload a logo, favicon, or link-preview image")
async def upload_image(request: Request, image: str) -> Any:
    if image not in brandkit.IMAGES:
        raise HTTPException(status_code=404, detail="No such image.")
    declared = int(request.headers.get("content-length") or 0)
    if declared > brandkit.IMAGE_LIMIT:
        return _refused(brandkit.BrandingRefused(["Images must be at most 2 MB."]))
    content = b""
    async for chunk in request.stream():
        content += chunk
        if len(content) > brandkit.IMAGE_LIMIT:
            return _refused(brandkit.BrandingRefused(["Images must be at most 2 MB."]))
    try:
        path = brandkit.store_image(config_of(request), image, content)
    except brandkit.BrandingRefused as exc:
        return _refused(exc)
    return envelope({"image": image, "path": path})
