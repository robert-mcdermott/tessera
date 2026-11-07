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


def _luminance(hex_color: str) -> float | None:
    """Relative luminance of a #rgb or #rrggbb color, or None for other notations."""
    value = hex_color.lstrip("#")
    if len(value) in (3, 4):
        value = "".join(c * 2 for c in value[:3])
    if len(value) not in (6, 8):
        return None
    try:
        channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    except ValueError:
        return None
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def theme_css(accent: str | None) -> str:
    """The configured accent, if any, as overrides of the palette's accent tokens.

    By day the accent is used as given; after dark it is lightened so it reads on dark
    surfaces. Text on the accent is white unless the accent is light (hex colors only).
    """
    if not accent:
        return "/* No accent configured: the palette's own accent applies. */\n"
    luminance = _luminance(accent) if accent.startswith("#") else None
    on_light = "oklch(18% 0.02 265)" if luminance is not None and luminance > 0.36 else "#ffffff"
    return (
        ":root {\n"
        f"  --brand-accent: light-dark({accent}, color-mix(in oklch, {accent} 58%, white));\n"
        f"  --brand-on-accent: light-dark({on_light}, oklch(17% 0.02 265));\n"
        "}\n"
    )


def install_theme(app: FastAPI) -> None:
    """``/theme.css``: the brand color from configuration (validated as plain CSS text)."""

    @app.get("/theme.css", include_in_schema=False)
    async def theme(request: Request) -> Response:
        accent = request.app.state.config.branding.accent_color
        return Response(
            theme_css(accent), media_type="text/css", headers={"Cache-Control": "no-cache"}
        )


def install_api_reference(app: FastAPI, root: Path = WEB_ROOT) -> None:
    """A self-hosted API reference at /api/docs (no third-party assets, unlike stock docs)."""

    @app.get("/api/docs", include_in_schema=False)
    async def api_reference() -> Response:
        return FileResponse(root / "api-docs.html", headers={"Cache-Control": "no-cache"})


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
