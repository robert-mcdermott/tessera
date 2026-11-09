"""The organization's look, set in the app and written to the configuration file (PLT-13).

**Admin → Branding** edits the ``[branding]`` section of ``tessera.toml``: the product name, the
default palette, the accent, the organization's own colors by day and by night, the logos, the
favicon, the picture for link previews, and the footer links. The configuration file stays the
source of truth: saving rewrites that section (its tables, ``[branding.palette.day]`` and
``.night``, included) and leaves everything else in the file as it was, comments and all.

A save is refused when any pair of colors that carries text falls below WCAG 2.2 AA in either
mode, or when the result would not load; the file is replaced atomically, and the one before is
kept beside it as ``tessera.toml.bak``.

Uploaded images are kept in ``brand/`` beside the configuration file. SVG is accepted only
without scripts, event handlers, embedded documents, or references to anything outside the file,
and every branding image is served with a policy that lets no script run.
"""

from __future__ import annotations

import hashlib
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tessera.branding import COLOR_NAMES, HEX, PAIRS, Colors, colors_for, contrast
from tessera.config import PALETTES, Config, ConfigError, load_config

BRAND_DIR = "brand"
IMAGES = ("logo_light", "logo_dark", "favicon", "social_image")
IMAGE_LIMIT = 2 * 1024 * 1024  # bytes; logos and icons are small
# What each image may be, by its first bytes (SVG is checked as text).
IMAGE_TYPES: dict[str, tuple[str, ...]] = {
    "logo_light": ("svg", "png", "jpeg", "webp"),
    "logo_dark": ("svg", "png", "jpeg", "webp"),
    "favicon": ("svg", "png", "ico"),
    "social_image": ("png", "jpeg"),
}
EXTENSIONS = {"svg": ".svg", "png": ".png", "jpeg": ".jpg", "webp": ".webp", "ico": ".ico"}
FOOTER_LINKS = 8

# A table header line: ``[branding]``, ``[branding.palette.day]``, ``[[x]]``.
_HEADER = re.compile(r"^\s*\[\[?\s*([A-Za-z0-9_.\-\" ]+?)\s*\]\]?\s*(?:#.*)?$")
_UNSAFE_SVG = re.compile(
    r"<\s*script|<\s*foreignObject|<\s*iframe|<\s*embed|<\s*object|\bon[a-z]+\s*=|"
    r"javascript:|<!ENTITY|(?:href|src)\s*=\s*[\"'](?!#|data:image/)",
    re.IGNORECASE,
)


class BrandingRefused(ValueError):
    """The proposed branding cannot be saved; ``problems`` says why."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Proposal:
    """Branding as the editor sends it: plain values, colors as hex."""

    product_name: str
    theme: str
    accent_color: str | None
    show_powered_by: bool
    day: dict[str, str]  # color name -> hex, only those set
    night: dict[str, str]
    footer_links: tuple[tuple[str, str], ...]
    images: dict[str, str | None]  # image -> path relative to the configuration file, or None


# --- reading -------------------------------------------------------------------------------------


def current(config: Config) -> Proposal:
    """The configured branding, as the editor shows it."""
    brand = config.branding
    base = config.path.parent if config.path else Path.cwd()

    def relative(path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            return str(path.relative_to(base))
        except ValueError:
            return str(path)

    day = {name: value for mode, name, value in brand.palette if mode == "day"}
    night = {name: value for mode, name, value in brand.palette if mode == "night"}
    return Proposal(
        product_name=brand.product_name,
        theme=brand.theme,
        accent_color=brand.accent_color,
        show_powered_by=brand.show_powered_by,
        day=day,
        night=night,
        footer_links=brand.footer_links,
        images={image: relative(getattr(brand, image)) for image in IMAGES},
    )


def proposal_from(body: dict[str, Any]) -> Proposal:
    """A proposal from the editor's JSON, with its shape checked (its values are checked later)."""
    problems: list[str] = []

    def text(key: str, default: str = "") -> str:
        value = body.get(key, default)
        if not isinstance(value, str):
            problems.append(f"{key} must be text")
            return default
        return value.strip()

    def colors(key: str) -> dict[str, str]:
        value = body.get(key) or {}
        if not isinstance(value, dict):
            problems.append(f"{key} must map color names to hex values")
            return {}
        return {str(k): str(v).strip().lower() for k, v in value.items() if v not in (None, "")}

    links = body.get("footer_links") or []
    footer: list[tuple[str, str]] = []
    if not isinstance(links, list):
        problems.append("footer_links must be a list")
    else:
        for item in links:
            if isinstance(item, dict):
                footer.append(
                    (str(item.get("label", "")).strip(), str(item.get("url", "")).strip())
                )
            else:
                problems.append("each footer link needs a label and an address")
    images = body.get("images") or {}
    if not isinstance(images, dict):
        problems.append("images must map each image to a path")
        images = {}
    accent = body.get("accent_color")
    if problems:
        raise BrandingRefused(problems)
    return Proposal(
        product_name=text("product_name", "Tessera"),
        theme=text("theme", "tessera") or "tessera",
        accent_color=str(accent).strip() if accent else None,
        show_powered_by=bool(body.get("show_powered_by", True)),
        day=colors("day"),
        night=colors("night"),
        footer_links=tuple(footer),
        images={image: (str(images[image]) if images.get(image) else None) for image in IMAGES},
    )


# --- checking ------------------------------------------------------------------------------------


def resolved(proposal: Proposal) -> tuple[Colors, Colors]:
    """The colors the proposal gives by day and by night, exactly as the app would draw them:
    Tessera's wherever it sets none, the accent lightened after dark unless set for both."""
    entries = [("day", k, v) for k, v in proposal.day.items() if k in COLOR_NAMES]
    entries += [("night", k, v) for k, v in proposal.night.items() if k in COLOR_NAMES]
    accent = (
        proposal.accent_color
        if proposal.accent_color and HEX.match(proposal.accent_color)
        else None
    )
    return colors_for(accent, entries)


def contrast_table(proposal: Proposal) -> list[dict[str, Any]]:
    """Each pair that carries text, by day and by night: its ratio, minimum, and verdict."""
    out: list[dict[str, Any]] = []
    for mode, colors in zip(("day", "night"), resolved(proposal), strict=True):
        for fg, bg, minimum, label in PAIRS:
            ratio = contrast(getattr(colors, fg), getattr(colors, bg))
            out.append(
                {
                    "mode": mode,
                    "foreground": fg,
                    "background": bg,
                    "label": label,
                    "ratio": round(ratio, 2),
                    "minimum": minimum,
                    "passes": ratio >= minimum,
                }
            )
    return out


def problems_of(proposal: Proposal, base: Path) -> list[str]:
    """Everything that stops the proposal from being saved."""
    problems: list[str] = []
    if not proposal.product_name or len(proposal.product_name) > 60:
        problems.append("The product name must be 1 to 60 characters.")
    if any(c in proposal.product_name for c in "<>\n\r\t"):
        problems.append("The product name must be plain text.")
    if proposal.theme not in PALETTES:
        problems.append(f"The palette must be one of {', '.join(PALETTES)}.")
    if proposal.accent_color and not HEX.match(proposal.accent_color):
        problems.append("The accent must be a hex color, such as #0b6e4f.")
    for mode, colors in (("day", proposal.day), ("night", proposal.night)):
        for name, value in colors.items():
            if name not in COLOR_NAMES:
                problems.append(f"{name} is not one of the palette's colors.")
            elif not HEX.match(value):
                problems.append(f"The {mode} {name} must be a hex color, such as #0b6e4f.")
    if len(proposal.footer_links) > FOOTER_LINKS:
        problems.append(f"At most {FOOTER_LINKS} footer links.")
    for label, url in proposal.footer_links:
        if not label or len(label) > 60:
            problems.append("Each footer link needs a label of 1 to 60 characters.")
        if not url.startswith(("https://", "mailto:")):
            problems.append(f"The address of {label or 'a footer link'} must start with https://.")
    for image, path in proposal.images.items():
        if path and not (base / path).is_file():
            problems.append(f"The {image.replace('_', ' ')} file {path} does not exist.")
    if not problems:
        for row in contrast_table(proposal):
            if not row["passes"]:
                problems.append(
                    f"By {row['mode']}, {row['label']} has a contrast of {row['ratio']}:1; "
                    f"it needs at least {row['minimum']}:1."
                )
    return problems


# --- writing -------------------------------------------------------------------------------------


def _string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    )
    return f'"{escaped}"'


def section(proposal: Proposal) -> str:
    """The ``[branding]`` section, as TOML."""
    lines = [
        "# Set in the app (Admin → Branding), which rewrites these tables when it saves: comments",
        "# in them are not kept. The file as it was before is beside this one, ending in .bak.",
        "[branding]",
        f"product_name = {_string(proposal.product_name)}",
    ]
    lines.append(f"theme = {_string(proposal.theme)}")
    if proposal.accent_color:
        lines.append(f"accent_color = {_string(proposal.accent_color)}")
    lines.append(f"show_powered_by = {'true' if proposal.show_powered_by else 'false'}")
    for image in IMAGES:
        path = proposal.images.get(image)
        if path:
            lines.append(f"{image} = {_string(path)}")
    if proposal.footer_links:
        lines.append("footer_links = [")
        for label, url in proposal.footer_links:
            lines.append(f"  {{ label = {_string(label)}, url = {_string(url)} }},")
        lines.append("]")
    for mode, colors in (("day", proposal.day), ("night", proposal.night)):
        if colors:
            lines += ["", f"[branding.palette.{mode}]"]
            lines += [f"{name} = {_string(colors[name])}" for name in COLOR_NAMES if name in colors]
    return "\n".join(lines) + "\n"


def _table_of(line: str) -> str | None:
    match = _HEADER.match(line)
    return match.group(1).replace('"', "").replace(" ", "") if match else None


def rewrite(text: str, new_section: str) -> str:
    """``text`` with its ``[branding]`` tables replaced by ``new_section``: everything else, and
    every comment outside those tables, as it was. The section goes where ``[branding]`` was, or
    at the end."""
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    out: list[str] = []

    def blank_before() -> None:
        if out and out[-1].strip():
            out.append("\n")

    placed = inside = False
    for line in lines:
        table = _table_of(line)
        if table is not None:
            inside = table == "branding" or table.startswith("branding.")
            if inside and not placed:
                blank_before()
                out += [new_section, "\n"]
                placed = True
        if not inside:
            out.append(line)
    if not placed:
        blank_before()
        out.append(new_section)
    return "".join(out)


def _without_branding(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k != "branding"}


def save(config: Config, proposal: Proposal) -> Config:
    """Write the proposal into the configuration file and return the configuration it loads to.

    Refused, with nothing written, when the proposal has problems, when the file sets branding
    in a way this cannot rewrite (dotted keys outside the section, say), or when the result would
    not load."""
    if config.path is None:
        raise BrandingRefused(
            ["This configuration was not read from a file, so it cannot be saved."]
        )
    path = config.path
    problems = problems_of(proposal, path.parent)
    if problems:
        raise BrandingRefused(problems)
    text = path.read_text("utf-8")
    new_text = rewrite(text, section(proposal))
    by_hand = "change it by hand, or ask whoever looks after the file"
    try:
        before, after = tomllib.loads(text), tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as exc:
        raise BrandingRefused(
            [f"The configuration file sets branding in a way the app cannot rewrite ({exc}); "
             f"{by_hand}."]
        ) from exc  # fmt: skip
    expected = tomllib.loads(section(proposal))["branding"]
    if after.get("branding") != expected or _without_branding(after) != _without_branding(before):
        raise BrandingRefused(
            [f"The configuration file sets branding outside its [branding] section; {by_hand}."]
        )
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(new_text, "utf-8")
        try:
            load_config(tmp)  # the result must load, as the server and every command read it
        except ConfigError as exc:
            raise BrandingRefused([f"The result would not load: {exc}"]) from exc
        path.with_name(f"{path.name}.bak").write_text(text, "utf-8")
        with tmp.open("rb") as handle:
            os.fsync(handle.fileno())
        tmp.replace(path)
    except OSError as exc:
        raise BrandingRefused(
            [f"The configuration file cannot be written here ({exc}); {by_hand}."]
        ) from exc
    finally:
        tmp.unlink(missing_ok=True)
    return load_config(path)


# --- images --------------------------------------------------------------------------------------


def image_kind(content: bytes) -> str | None:
    """What an uploaded image is, by its first bytes."""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"
    if content[:4] == b"\x00\x00\x01\x00":
        return "ico"
    head = content[:2048].lstrip().lower()
    if head.startswith((b"<svg", b"<?xml")) and b"<svg" in content[:4096].lower():
        return "svg"
    return None


def store_image(config: Config, image: str, content: bytes) -> str:
    """Keep an uploaded image in ``brand/`` beside the configuration file; return its path,
    relative to the file, for the proposal. Nothing is configured until the branding is saved."""
    if image not in IMAGES:
        raise BrandingRefused([f"There is no image called {image}."])
    if config.path is None:
        raise BrandingRefused(["This configuration was not read from a file."])
    if not content:
        raise BrandingRefused(["The file is empty."])
    if len(content) > IMAGE_LIMIT:
        raise BrandingRefused([f"Images must be at most {IMAGE_LIMIT // (1024 * 1024)} MB."])
    kind = image_kind(content)
    if kind not in IMAGE_TYPES[image]:
        allowed = ", ".join(k.upper() for k in IMAGE_TYPES[image])
        raise BrandingRefused([f"The {image.replace('_', ' ')} must be {allowed}."])
    if kind == "svg":
        try:
            svg = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BrandingRefused(["An SVG must be UTF-8 text."]) from exc
        if _UNSAFE_SVG.search(svg):
            raise BrandingRefused(
                ["This SVG has scripts, event handlers, embedded documents, or links to other "
                 "files; export it again as a plain image."]
            )  # fmt: skip
    # Named by its content, so a browser never shows an older picture from its cache.
    name = f"{image.replace('_', '-')}-{hashlib.sha256(content).hexdigest()[:10]}{EXTENSIONS[kind]}"
    folder = config.path.parent / BRAND_DIR
    tmp = folder / f".{name}.tmp"
    try:
        folder.mkdir(exist_ok=True)
        tmp.write_bytes(content)
        tmp.replace(folder / name)
    except OSError as exc:
        raise BrandingRefused(
            [f"Images cannot be kept beside the configuration file ({exc})."]
        ) from exc
    return f"{BRAND_DIR}/{name}"


__all__ = [
    "IMAGES",
    "BrandingRefused",
    "Proposal",
    "contrast_table",
    "current",
    "problems_of",
    "proposal_from",
    "resolved",
    "rewrite",
    "save",
    "section",
    "store_image",
]
