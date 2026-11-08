"""Turn the assistant on in the web app, only when ``[ai] enabled``: its routes and their access
policies, and the front-end module the app loads because ``/api/v1/meta`` names it. When off,
none of this is installed, so neither the app, nor the API document, nor any file served says
that an assistant exists (AI-07)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

STATIC = Path(__file__).parent / "static"
MODULE = "/js/ai/assistant.js"
TYPES = {".js": "text/javascript", ".css": "text/css"}


def install_ai(app: FastAPI) -> None:
    from tessera.api.routers import ai as ai_router

    app.include_router(ai_router.router)
    app.state.extra_policies = {**getattr(app.state, "extra_policies", {}),
                                **ai_router.POLICIES}  # fmt: skip
    app.state.extensions = [*getattr(app.state, "extensions", []), MODULE]

    @app.get("/js/ai/{name}", include_in_schema=False)
    async def assistant_file(name: str) -> FileResponse:
        path = STATIC / name
        if path.suffix not in TYPES or path.parent != STATIC or not path.is_file():
            raise HTTPException(status_code=404, detail="Not found.")
        return FileResponse(path, media_type=TYPES[path.suffix],
                            headers={"Cache-Control": "no-cache"})  # fmt: skip


__all__ = ["install_ai"]
