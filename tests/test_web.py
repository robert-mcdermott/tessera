"""The web app's static assets: safety rules, module wiring, size budget, and theming.

These run without a browser. When Node.js is installed they also syntax-check every
module and run the JavaScript unit tests in tests/js.
"""

from __future__ import annotations

import gzip
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tessera.api.security import SECURITY_HEADERS
from tessera.api.static import WEB_ROOT
from tessera.config import load_config

JS_ROOT = WEB_ROOT / "js"
CSS_ROOT = WEB_ROOT / "css"
JS_FILES = sorted(JS_ROOT.rglob("*.js"))
CSS_FILES = sorted(CSS_ROOT.rglob("*.css"))
HTML_FILES = sorted(WEB_ROOT.glob("*.html"))
NODE = shutil.which("node")

# The first-load budget for the app shell (the scripts the app imports at start, and every
# style, gzipped), and a budget for each part loaded on first use (such as the graph views).
SHELL_BUDGET_BYTES = 100 * 1024
LAZY_BUDGET_BYTES = 40 * 1024

IMPORT = re.compile(r"""^\s*import\s+(?:\{([^}]*)\}\s+from\s+)?["']([^"']+)["']""", re.M)
DYNAMIC_IMPORT = re.compile(r"""import\(\s*["']([^"']+)["']\s*\)""")
WORKER_URL = re.compile(r"""new\s+URL\(\s*["']([^"']+)["']\s*,\s*import\.meta\.url\s*\)""")
EXPORT_NAME = re.compile(
    r"^export\s+(?:async\s+)?(?:function\*?|const|let|class)\s+([A-Za-z_$][\w$]*)", re.M
)
EXPORT_LIST = re.compile(r"^export\s*\{([^}]*)\}", re.M)


def _rel(path: Path) -> str:
    return path.relative_to(WEB_ROOT).as_posix()


def test_assets_exist() -> None:
    assert (WEB_ROOT / "index.html").is_file()
    assert (JS_ROOT / "app.js").is_file()
    assert len(JS_FILES) > 10
    assert {p.name for p in CSS_FILES} >= {"tokens.css", "base.css", "components.css", "pages.css"}


@pytest.mark.parametrize("path", HTML_FILES, ids=_rel)
def test_html_has_no_inline_script_or_style(path: Path) -> None:
    """The content security policy forbids inline code, so none may creep in."""
    text = path.read_text("utf-8")
    for tag in re.findall(r"<script\b[^>]*>", text, re.I):
        assert "src=" in tag, f"inline <script> in {path.name}: {tag}"
    assert not re.search(r"<style\b", text, re.I), f"<style> element in {path.name}"
    assert not re.search(r"\sstyle\s*=", text, re.I), f"style attribute in {path.name}"
    assert not re.search(r"\son[a-z]+\s*=", text, re.I), f"inline event handler in {path.name}"


@pytest.mark.parametrize("path", HTML_FILES + CSS_FILES + JS_FILES, ids=_rel)
def test_no_resources_load_from_other_origins(path: Path) -> None:
    """Hyperlinks to public sources are fine; fetching code, styles, fonts or images is not."""
    text = path.read_text("utf-8")
    patterns = [
        r"<(script|img|iframe|source|video|audio)\b[^>]*\ssrc\s*=\s*[\"']?(https?:)?//",
        r"<link\b[^>]*\shref\s*=\s*[\"']?(https?:)?//",
        r"url\(\s*[\"']?(https?:)?//",
        r"@import\s+(url\()?\s*[\"']?(https?:)?//",
        r"""\bimport\s*(\(|[^;]*?\bfrom\s*)\s*["'](https?:)?//""",
        r"""\bfetch\(\s*["'`](https?:)?//""",
        r"fonts\.(googleapis|gstatic)\.com",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        assert match is None, f"{_rel(path)} loads from another origin: {match.group(0)!r}"


@pytest.mark.parametrize("path", JS_FILES, ids=_rel)
def test_scripts_never_parse_html(path: Path) -> None:
    """Data reaches the page only as text nodes. These APIs would parse it as markup."""
    text = path.read_text("utf-8")
    for pattern in (
        r"\.innerHTML\s*=",
        r"\.outerHTML\s*=",
        r"insertAdjacentHTML",
        r"document\.write",
        r"\beval\(",
        r"new\s+Function\(",
        r"createContextualFragment",
        r"""setAttribute\(\s*["']style["']""",
        r"\.style\.cssText",
    ):
        assert not re.search(pattern, text), f"{_rel(path)} uses {pattern}"


def _exports(path: Path) -> set[str]:
    text = path.read_text("utf-8")
    names = set(EXPORT_NAME.findall(text))
    for group in EXPORT_LIST.findall(text):
        for item in group.split(","):
            item = item.strip()
            if item:
                names.add(item.split(" as ")[-1].strip())
    return names


@pytest.mark.parametrize("path", JS_FILES, ids=_rel)
def test_module_imports_resolve(path: Path) -> None:
    """A broken import stops the whole app from starting, so check every one."""
    text = path.read_text("utf-8")
    specifiers = [(names, spec) for names, spec in IMPORT.findall(text)]
    specifiers += [("", spec) for spec in DYNAMIC_IMPORT.findall(text)]
    specifiers += [("", spec) for spec in WORKER_URL.findall(text)]
    for names, spec in specifiers:
        assert spec.startswith((".", "/")), f"{_rel(path)} imports a bare module {spec!r}"
        target = (WEB_ROOT / spec.lstrip("/")) if spec.startswith("/") else path.parent / spec
        target = target.resolve()
        assert target.is_file(), f"{_rel(path)} imports missing {spec}"
        assert target.is_relative_to(WEB_ROOT.resolve())
        wanted = {n.split(" as ")[0].strip() for n in names.split(",") if n.strip()}
        missing = wanted - _exports(target)
        assert not missing, f"{_rel(path)} imports {sorted(missing)} that {spec} does not export"


def test_index_references_resolve(client: TestClient) -> None:
    index = (WEB_ROOT / "index.html").read_text("utf-8")
    refs = re.findall(r"""(?:href|src)\s*=\s*["'](/[^"'#]*)["']""", index)
    assert "/js/app.js" in refs and "/theme.css" in refs
    for ref in refs:
        response = client.get(ref)
        assert response.status_code == 200, ref


def _static_closure(roots: list[Path]) -> set[Path]:
    """Every module reached from the roots by static imports (dynamic ones load later)."""
    seen: set[Path] = set()
    todo = [p.resolve() for p in roots]
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        for _, spec in IMPORT.findall(path.read_text("utf-8")):
            target = (WEB_ROOT / spec.lstrip("/")) if spec.startswith("/") else path.parent / spec
            todo.append(target.resolve())
    return seen


def _gzipped(paths: set[Path] | list[Path]) -> int:
    return sum(len(gzip.compress(p.read_bytes(), 9)) for p in paths)


def test_reduced_motion_turns_transitions_off() -> None:
    """Not a tiny duration: with transition-property "all", every style change would become a
    transition, and the graph's colors (read back from computed styles) would come out wrong."""
    css = (CSS_ROOT / "base.css").read_text("utf-8")
    block = css[css.index("@media (prefers-reduced-motion: reduce)") :]
    block = block[: block.index("}\n}") + 3]
    assert "transition-duration: 0s" in block, block


def test_app_shell_fits_the_budget() -> None:
    shell = _static_closure([JS_ROOT / "app.js"])
    total = _gzipped(shell) + _gzipped(CSS_FILES)
    assert total <= SHELL_BUDGET_BYTES, f"app shell is {total:,} bytes gzipped"


def test_parts_loaded_on_first_use_fit_their_budget() -> None:
    shell = _static_closure([JS_ROOT / "app.js"])
    lazy = []
    for path in JS_FILES:
        text = path.read_text("utf-8")
        lazy += [path.parent / spec for spec in DYNAMIC_IMPORT.findall(text)]
        lazy += [path.parent / spec for spec in WORKER_URL.findall(text)]
    assert lazy, "nothing is loaded on first use"
    for root in lazy:
        part = _static_closure([root]) - shell
        size = _gzipped(part)
        assert size <= LAZY_BUDGET_BYTES, f"{_rel(root.resolve())} adds {size:,} bytes gzipped"
    pages = [
        WEB_ROOT / src.lstrip("/")
        for html in HTML_FILES
        for src in re.findall(r"""<script[^>]+src=["'](/js/[^"']+)["']""", html.read_text("utf-8"))
    ]
    every = _static_closure([*pages, *lazy])
    unused = {p.resolve() for p in JS_FILES} - every
    assert not unused, f"modules nothing loads: {sorted(_rel(p) for p in unused)}"


def _client_with(config_file: Path, branding: str) -> TestClient:
    from tessera.api.app import create_app

    text = config_file.read_text("utf-8").replace('theme = "tessera"\n', branding, 1)
    config_file.write_text(text, "utf-8")
    return TestClient(create_app(load_config(config_file, env={})), base_url="http://localhost")


def test_theme_css_without_an_accent(client: TestClient) -> None:
    response = client.get("/theme.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert "--brand-accent" not in response.text  # the palette's accent applies
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value


def test_theme_css_uses_configured_accent(config_file: Path, instance: object) -> None:
    with _client_with(
        config_file, 'theme = "cassette"\naccent_color = "oklch(0.6 0.2 150)"\n'
    ) as client:
        css = client.get("/theme.css").text
        meta = client.get("/api/v1/meta").json()["data"]["product"]
    accent = "oklch(0.6 0.2 150)"
    assert f"--brand-accent: light-dark({accent}, color-mix(in oklch, {accent} 58%, white));" in css
    assert "--brand-on-accent: light-dark(#ffffff," in css
    assert meta["theme"] == "cassette" and meta["accent_color"] == "oklch(0.6 0.2 150)"


def test_light_accent_gets_dark_text(config_file: Path, instance: object) -> None:
    with _client_with(config_file, 'theme = "tessera"\naccent_color = "#FFD84D"\n') as client:
        css = client.get("/theme.css").text
    assert "--brand-on-accent: light-dark(oklch(18% 0.02 265)," in css


def test_every_configurable_palette_is_styled() -> None:
    from tessera.config import PALETTES

    css = (CSS_ROOT / "palettes.css").read_text("utf-8")
    prefs = (JS_ROOT / "core" / "prefs.js").read_text("utf-8")
    for name in PALETTES:
        assert f'id: "{name}"' in prefs, name
        if name != "tessera":  # the default lives in tokens.css
            assert f':root[data-palette="{name}"]' in css, name


_COLOR = r"#[0-9a-fA-F]{6}|oklch\([^)]*\)"
_PAIR = re.compile(
    rf"--(?P<name>[\w-]+):\s*light-dark\((?P<light>{_COLOR}),\s*(?P<dark>{_COLOR})\)"
)


def _oklab(color: str) -> tuple[float, float, float]:
    """OKLab coordinates of a hex or oklch() color."""
    if color.startswith("oklch"):
        lightness, chroma, hue = (float(x) for x in re.findall(r"[\d.]+", color)[:3])
        lightness = lightness / 100 if lightness > 1 else lightness
        return (
            lightness,
            chroma * math.cos(math.radians(hue)),
            chroma * math.sin(math.radians(hue)),
        )
    rgb = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    l_, m_, s_ = (
        (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3),
        (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3),
        (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3),
    )
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def _hue(color: str) -> float:
    """The OKLCH hue of a hex or oklch() color, in degrees."""
    _, a, b = _oklab(color)
    return math.degrees(math.atan2(b, a)) % 360


def _contrast(one: str, two: str) -> float:
    """WCAG contrast ratio between two colors."""

    def luminance(color: str) -> float:
        lightness, a, b = _oklab(color)
        l_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
        m_ = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
        s_ = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
        rgb = (
            4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
            -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
            -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_,
        )
        r, g, b_ = (min(1.0, max(0.0, c)) for c in rgb)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b_

    high, low = sorted((luminance(one), luminance(two)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _palette_colors(name: str) -> dict[str, tuple[str, str]]:
    """Light and dark values of a palette's color tokens, over the defaults it inherits."""
    tokens = (CSS_ROOT / "tokens.css").read_text("utf-8")
    start = tokens.index(":root {\n")
    block = tokens[start : tokens.index("\n}", start)]
    colors = {m["name"]: (m["light"], m["dark"]) for m in _PAIR.finditer(block)}
    if name != "tessera":
        css = (CSS_ROOT / "palettes.css").read_text("utf-8")
        start = css.index(f':root[data-palette="{name}"] {{')
        block = css[start : css.index("\n}", start)]
        colors.update({m["name"]: (m["light"], m["dark"]) for m in _PAIR.finditer(block)})
    return colors


@pytest.mark.parametrize("palette", ["tessera", "harbor", "cassette"])
def test_record_colors_stay_clear_of_status_colors(palette: str) -> None:
    """A record type's color must not read as a status: hues at least 28 degrees apart."""
    colors = _palette_colors(palette)
    for mode, label in enumerate(("light", "dark")):
        for record in ("ent-person", "ent-pub", "ent-grant", "ent-study", "ent-concept"):
            for status in ("ok", "warn", "danger"):
                gap = abs(_hue(colors[record][mode]) - _hue(colors[status][mode])) % 360
                gap = min(gap, 360 - gap)
                assert gap >= 28, f"{palette} ({label}): --{record} is {gap:.0f}° from --{status}"


@pytest.mark.parametrize("palette", ["tessera", "harbor", "cassette"])
def test_series_colors_are_distinct_and_visible(palette: str) -> None:
    """Groups in a graph must be told apart, and seen: OKLab distance at least 0.1 between
    every two series colors, and 3:1 contrast against cards, in light and dark."""
    colors = _palette_colors(palette)
    series = [f"series-{i}" for i in range(6)]
    for mode, label in enumerate(("light", "dark")):
        for i, one in enumerate(series):
            ratio = _contrast(colors[one][mode], colors["surface-1"][mode])
            assert ratio >= 3.0, f"{palette} ({label}): --{one} is {ratio:.1f}:1 on cards"
            for two in series[i + 1 :]:
                gap = math.dist(_oklab(colors[one][mode]), _oklab(colors[two][mode]))
                assert gap >= 0.1, f"{palette} ({label}): --{one} and --{two} are {gap:.3f} apart"


def test_api_reference_page(client: TestClient) -> None:
    response = client.get("/api/docs")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "API reference" in response.text


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
@pytest.mark.parametrize("path", JS_FILES, ids=_rel)
def test_modules_parse(path: Path) -> None:
    result = subprocess.run(
        [NODE, "--input-type=module", "--check"],
        input=path.read_bytes(),
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_javascript_unit_tests() -> None:
    files = sorted(str(p) for p in (Path(__file__).parent / "js").glob("*.test.mjs"))
    assert files
    result = subprocess.run([NODE, "--test", *files], capture_output=True, timeout=120, check=False)
    assert result.returncode == 0, result.stdout.decode("utf-8", "replace")[-4000:]


def test_configured_logo_is_served(config_file: Path, instance: object) -> None:
    logo = config_file.parent / "logo.svg"
    logo.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>', "utf-8")
    with _client_with(config_file, 'theme = "tessera"\nlogo_light = "logo.svg"\n') as client:
        meta = client.get("/api/v1/meta").json()["data"]["product"]
        light = client.get("/branding/logo-light")
        dark = client.get("/branding/logo-dark")  # falls back to the light version
        other = client.get("/branding/anything")
    assert meta["logo"] == {"light": "/branding/logo-light", "dark": "/branding/logo-dark"}
    assert light.status_code == 200 and light.headers["content-type"].startswith("image/svg+xml")
    assert dark.status_code == 200 and dark.content == light.content
    assert other.status_code == 404
    assert light.headers["content-security-policy"] == SECURITY_HEADERS["Content-Security-Policy"]


def test_no_logo_by_default(client: TestClient) -> None:
    assert client.get("/api/v1/meta").json()["data"]["product"]["logo"] is None
    assert client.get("/branding/logo-light").status_code == 404
