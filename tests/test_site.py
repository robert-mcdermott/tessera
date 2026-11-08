"""The documentation site builds, covers every guide, and has no broken links."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_the_site_builds_with_no_broken_links(tmp_path: Path) -> None:
    out = tmp_path / "site"
    result = subprocess.run(
        [NODE, str(REPO / "docs" / "site" / "build.mjs"), "--out", str(out)],
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    for guide in (REPO / "docs").glob("*.md"):
        if guide.name != "README.md":
            assert (out / f"{guide.stem}.html").is_file(), f"{guide.name} is not in the site's menu"
    for page in ("index.html", "api-reference.html", "changelog.html"):
        assert (out / page).is_file()
    search = json.loads((out / "assets" / "search.json").read_text("utf-8"))
    assert {entry["page"] for entry in search} >= {"curation", "api-reference", "discovery"}
    index = (out / "index.html").read_text("utf-8")
    assert "<script>" not in index and "style=" not in index  # no inline code
