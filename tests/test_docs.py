"""The documentation stays true: generated references are current and links resolve.

Regenerate docs/cli.md and docs/openapi.json with:

    UPDATE_DOCS=1 uv run pytest tests/test_docs.py
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tessera.cli import build_parser
from tessera.cli.reference import render

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
UPDATE = os.environ.get("UPDATE_DOCS") == "1"
PAGES = [
    REPO / "README.md",
    REPO / "CONTRIBUTING.md",
    REPO / "CHANGELOG.md",
    *sorted(DOCS.glob("*.md")),
    *sorted((REPO / "spec").rglob("*.md")),
]


def _check_generated(path: Path, text: str) -> None:
    if UPDATE:
        path.write_text(text, "utf-8")
    assert path.is_file(), (
        f"{path.name} is missing; run UPDATE_DOCS=1 uv run pytest tests/test_docs.py"
    )
    assert path.read_text("utf-8") == text, (
        f"{path.name} is out of date; run UPDATE_DOCS=1 uv run pytest tests/test_docs.py"
    )


def test_command_reference_is_current() -> None:
    _check_generated(DOCS / "cli.md", render(build_parser()))


def test_openapi_document_is_current(client: TestClient) -> None:
    spec = client.get("/api/openapi.json").json()
    _check_generated(DOCS / "openapi.json", json.dumps(spec, indent=2, ensure_ascii=False) + "\n")


def _slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    text = re.sub(r"`", "", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    text = re.sub(r"```.*?```", "", path.read_text("utf-8"), flags=re.S)
    return {_slug(m) for m in re.findall(r"^#{1,6}\s+(.+)$", text, re.M)}


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.relative_to(REPO).as_posix())
def test_relative_links_resolve(page: Path) -> None:
    text = re.sub(r"```.*?```", "", page.read_text("utf-8"), flags=re.S)
    text = re.sub(r"`[^`\n]*`", "", text)  # examples in inline code are not links
    for target in re.findall(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", text):
        if re.match(r"^[a-z]+:", target) or target.startswith("<"):
            continue  # external links and autolinks
        file_part, _, anchor = target.partition("#")
        linked = (page.parent / file_part).resolve() if file_part else page
        assert linked.exists(), f"{page.name} links to missing {target}"
        if anchor and linked.suffix == ".md":
            assert anchor in _anchors(linked), f"{page.name} links to missing anchor {target}"


def test_every_guide_is_listed() -> None:
    index = (DOCS / "README.md").read_text("utf-8")
    for guide in DOCS.glob("*.md"):
        if guide.name != "README.md":
            assert f"]({guide.name})" in index, f"docs/README.md does not list {guide.name}"
