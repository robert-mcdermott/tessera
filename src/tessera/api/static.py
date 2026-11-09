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


# Where each brand color goes among the app's design tokens (web/static/css/tokens.css).
PALETTE_TOKENS = {
    "bg": ("--bg",),
    "surface": ("--surface-1",),
    "text": ("--text",),
    "muted": ("--text-muted", "--text-subtle"),
    "border": ("--border",),
    "accent": ("--brand-accent",),
    "on_accent": ("--brand-on-accent",),
    "stripe": ("--brand-stripe",),
    "person": ("--ent-person",),
    "publication": ("--ent-pub",),
    "grant": ("--ent-grant",),
    "study": ("--ent-study",),
}
SOFT = {"person": "--ent-person-soft", "publication": "--ent-pub-soft",
        "grant": "--ent-grant-soft", "study": "--ent-study-soft"}  # fmt: skip


def palette_css(colors: dict[str, tuple[str, str]]) -> str:
    """The organization's palette ([branding.palette]) as overrides of the app's tokens.
    Values are validated hex colors, so the text is safe to serve as CSS."""
    if not colors:
        return ""
    lines = []
    for name, (day, night) in sorted(colors.items()):
        for token in PALETTE_TOKENS[name]:
            lines.append(f"  {token}: light-dark({day}, {night});")
        if name in SOFT:
            lines.append(
                f"  {SOFT[name]}: light-dark(color-mix(in oklch, {day} 9%, white), "
                f"color-mix(in oklch, {night} 20%, black));"
            )
    return ":root {\n" + "\n".join(lines) + "\n}\n"


def install_theme(app: FastAPI) -> None:
    """``/theme.css``: the brand color and palette from configuration (validated values)."""
    from tessera.branding import palette

    @app.get("/theme.css", include_in_schema=False)
    async def theme(request: Request) -> Response:
        brand = request.app.state.config.branding
        day, night = palette(request.app.state.config)
        names = {name for _, name, _ in brand.palette}
        colors = {n: (getattr(day, n), getattr(night, n)) for n in names}
        return Response(
            theme_css(brand.accent_color) + palette_css(colors),
            media_type="text/css",
            headers={"Cache-Control": "no-cache"},
        )


FAVICON_TYPES = {".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}
# Branding images are pictures, never documents: whatever an SVG holds, nothing in it runs.
IMAGE_POLICY = (
    "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"
)


def install_branding(app: FastAPI, root: Path = WEB_ROOT) -> None:
    """``/branding/logo-light``, ``/branding/logo-dark``, ``/branding/favicon``, and
    ``/branding/social-image``: the configured files (the favicon falls back to Tessera's
    mark), served so that no script in them can run."""
    from tessera.config import LOGO_TYPES

    @app.get("/branding/{variant}", include_in_schema=False)
    async def logo(request: Request, variant: str) -> Response:
        brand = request.app.state.config.branding
        if variant == "favicon":
            icon = brand.favicon if brand.favicon and brand.favicon.is_file() else None
            path = icon or root / "img" / "tessera-mark.svg"
            return FileResponse(
                path,
                media_type=FAVICON_TYPES.get(path.suffix.lower(), "image/svg+xml"),
                headers={"Cache-Control": "no-cache", "Content-Security-Policy": IMAGE_POLICY},
            )
        path = {
            "logo-light": brand.logo_light,
            "logo-dark": brand.logo_dark or brand.logo_light,
            "social-image": brand.social_image,
        }.get(variant)
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="No such logo.")
        media_type = LOGO_TYPES.get(path.suffix.lower())
        if media_type is None:
            raise HTTPException(status_code=404, detail="No such logo.")
        headers = {"Cache-Control": "no-cache", "Content-Security-Policy": IMAGE_POLICY}
        return FileResponse(path, media_type=media_type, headers=headers)


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
