"""The organization's colors, by day and night, and the contrast checks they must pass.

The public site is drawn from this palette, and ``tessera doctor`` checks it: every pair of
colors that carries text must meet WCAG 2.2 level AA (4.5:1 for text, 3:1 for large text and
interface parts), in both modes. Colors are written as hex (``#4f46e5``) so they can be
measured exactly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields, replace

from tessera.config import Config

HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


@dataclass(frozen=True)
class Colors:
    bg: str  # the page
    surface: str  # cards and panels
    text: str
    muted: str  # secondary text
    border: str
    accent: str  # links, buttons, highlights
    on_accent: str  # text on the accent
    stripe: str  # the thin band under the header
    person: str  # record types, as text and icons
    publication: str
    grant: str
    study: str


DAY = Colors(
    bg="#f8f9fb", surface="#ffffff", text="#1b1f2a", muted="#525a6b", border="#e2e5ec",
    accent="#4f46e5", on_accent="#ffffff", stripe="#4f46e5",
    person="#4c44c9", publication="#0e6a6e", grant="#735600", study="#ab2d66",
)  # fmt: skip
NIGHT = Colors(
    bg="#0f1115", surface="#171a21", text="#eef0f4", muted="#b2b8c4", border="#2a2f38",
    accent="#a5a3f7", on_accent="#15122e", stripe="#a5a3f7",
    person="#b4b1ff", publication="#74d4cf", grant="#e8c55c", study="#f293c2",
)  # fmt: skip

# (foreground, background, minimum ratio, what it is)
PAIRS = (
    ("text", "bg", 4.5, "text on the page"),
    ("text", "surface", 4.5, "text on cards"),
    ("muted", "bg", 4.5, "secondary text on the page"),
    ("muted", "surface", 4.5, "secondary text on cards"),
    ("accent", "bg", 4.5, "links on the page"),
    ("accent", "surface", 4.5, "links on cards"),
    ("on_accent", "accent", 4.5, "text on the accent"),
    ("stripe", "bg", 3.0, "the header stripe against the page"),
    ("person", "surface", 4.5, "people's color on cards"),
    ("publication", "surface", 4.5, "publications' color on cards"),
    ("grant", "surface", 4.5, "grants' color on cards"),
    ("study", "surface", 4.5, "studies' color on cards"),
)


def _rgb(color: str) -> tuple[float, float, float]:
    value = color.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    r, g, b = (int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return r, g, b


def luminance(color: str) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def mix(a: str, b: str, share_of_b: float) -> str:
    """``a`` with ``share_of_b`` of ``b`` mixed in, as hex."""
    ra, ga, ba = _rgb(a)
    rb, gb, bb = _rgb(b)
    channels = (x + (y - x) * share_of_b for x, y in ((ra, rb), (ga, gb), (ba, bb)))
    return "#" + "".join(f"{round(c * 255):02x}" for c in channels)


def readable_on(color: str) -> str:
    """White or near-black, whichever reads better on ``color``."""
    light, dark = "#ffffff", "#15122e"
    return light if contrast(light, color) >= contrast(dark, color) else dark


def palette(config: Config) -> tuple[Colors, Colors]:
    """(day, night): the defaults, with the organization's own colors over them."""
    return colors_for(config.branding.accent_color, config.branding.palette)


def colors_for(
    accent_color: str | None, entries: tuple[tuple[str, str, str], ...] | list[tuple[str, str, str]]
) -> tuple[Colors, Colors]:
    """(day, night) from an accent and (mode, name, hex) entries, as the app draws them."""
    day, night = DAY, NIGHT
    if accent_color and HEX.match(accent_color):
        # By night the accent is lightened so it reads on dark surfaces, as in the app.
        lighter = mix(accent_color, "#ffffff", 0.42)
        day = replace(day, accent=accent_color, stripe=accent_color,
                      on_accent=readable_on(accent_color))  # fmt: skip
        night = replace(night, accent=lighter, stripe=lighter, on_accent=readable_on(lighter))
    for mode, name, value in entries:
        if HEX.match(value):
            if mode == "day":
                day = replace(day, **{name: value})
            else:
                night = replace(night, **{name: value})
    return day, night


def configured(config: Config) -> dict[str, tuple[str, str]]:
    """The colors the organization set in either mode: {name: (day, night)}."""
    day, night = palette(config)
    names = {name for _, name, _ in config.branding.palette}
    if config.branding.accent_color and HEX.match(config.branding.accent_color):
        names |= {"accent", "stripe"}
    return {n: (getattr(day, n), getattr(night, n)) for n in sorted(names)}


def contrast_problems(config: Config) -> list[str]:
    """Every color pair below WCAG AA, as sentences."""
    problems = []
    for mode, colors in zip(("day", "night"), palette(config), strict=True):
        for fg, bg, minimum, what in PAIRS:
            ratio = contrast(getattr(colors, fg), getattr(colors, bg))
            if ratio < minimum:
                problems.append(
                    f"{mode}: {what} ({fg} {getattr(colors, fg)} on {bg} {getattr(colors, bg)}) "
                    f"has contrast {ratio:.2f}:1, below {minimum:g}:1"
                )
    return problems


COLOR_NAMES = tuple(f.name for f in fields(Colors))

__all__ = ["COLOR_NAMES", "DAY", "NIGHT", "PAIRS", "Colors", "colors_for", "configured", "contrast",
           "contrast_problems", "luminance", "palette"]  # fmt: skip
