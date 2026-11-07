"""Serve the vanilla web app, with client-side routes falling back to ``index.html``."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

WEB_ROOT = Path(str(resources.files("tessera.web").joinpath("static")))


def resolve_static(root: Path, request_path: str) -> Path | None:
    """Map a URL path to a file inside ``root``; None if absent or outside the root."""
    root = root.resolve()
    if "\x00" in request_path:
        return None
    candidate = (root / request_path.lstrip("/")).resolve()
    if not candidate.is_relative_to(root):
        return None
    return candidate if candidate.is_file() else None


def install_static(app: FastAPI, root: Path = WEB_ROOT) -> None:
    index = root / "index.html"

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def web_app(path: str, request: Request) -> Response:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="No such API endpoint.")
        found = resolve_static(root, path) if path else None
        if found is not None:
            return FileResponse(found, headers={"Cache-Control": "no-cache"})
        last_segment = path.rsplit("/", 1)[-1]
        if "." in last_segment:  # looks like a file that does not exist
            raise HTTPException(status_code=404, detail="Not found.")
        # Anything else is a client-side route handled by the web app's router.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
