"""The web app's static assets: safety rules, module wiring, size budget, and theming.

These run without a browser. When Node.js is installed they also syntax-check every
module and run the JavaScript unit tests in tests/js.
"""

from __future__ import annotations

import gzip
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

# The first-load budget for the app shell (all scripts and styles, gzipped).
SHELL_BUDGET_BYTES = 100 * 1024

IMPORT = re.compile(r"""^\s*import\s+(?:\{([^}]*)\}\s+from\s+)?["']([^"']+)["']""", re.M)
DYNAMIC_IMPORT = re.compile(r"""import\(\s*["']([^"']+)["']\s*\)""")
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


def test_app_shell_fits_the_budget() -> None:
    total = sum(len(gzip.compress(p.read_bytes(), 9)) for p in JS_FILES + CSS_FILES)
    assert total <= SHELL_BUDGET_BYTES, f"app shell is {total:,} bytes gzipped"


def test_theme_css_uses_configured_accent(config_file: Path, instance: object) -> None:
    text = config_file.read_text("utf-8")
    config_file.write_text(
        re.sub(r'accent_color\s*=\s*"[^"]*"', 'accent_color = "oklch(0.6 0.2 150)"', text),
        "utf-8",
    )
    from tessera.api.app import create_app

    with TestClient(create_app(load_config(config_file, env={}))) as client:
        response = client.get("/theme.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert response.text == ":root { --brand: oklch(0.6 0.2 150); }\n"
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value


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
