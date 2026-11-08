"""``tessera publish``: a static public site from an allowlisted projection (PLT-07).

The site is plain files: a home page, a page for every group and person who opted in, indexes
of both, a sitemap, and a stylesheet in the organization's colors. It is built in a fresh
folder, checked (every page is valid enough to read with a screen reader: one language, one
title, one main heading, headings in order, labelled links and images, colors that meet WCAG
AA), and only then put in place, so a failed build never replaces a good site. Any web server
can serve it; the internal app stays off the internet.

Openings close on their dates, so publish daily (after ``sync``) to keep the site current.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine

from tessera.config import Config
from tessera.home import DataHome
from tessera.publish import pages as site_pages
from tessera.publish.checks import check_site
from tessera.publish.projection import project
from tessera.publish.style import favicon, stylesheet


@dataclass
class PublishResult:
    out: Path
    pages: int = 0
    groups: int = 0
    people: int = 0
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    written: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "out": str(self.out),
            "pages": self.pages,
            "groups": self.groups,
            "people": self.people,
            "problems": self.problems,
            "notes": self.notes,
            "written": self.written,
        }


def out_dir(config: Config, home: DataHome) -> Path:
    return config.publish.out or (home.root / "public")


def publish(
    config: Config,
    home: DataHome,
    engine: Engine,
    *,
    out: Path | None = None,
    check_only: bool = False,
    today: date | None = None,
) -> PublishResult:
    target = (out or out_dir(config, home)).resolve()
    site = project(config, home, engine, today=today)
    files = site_pages.build(site, config)
    files["assets/site.css"] = stylesheet(config)
    brand = config.branding
    if brand.favicon and brand.favicon.is_file():
        files[site.favicon] = brand.favicon.read_bytes()
    else:
        files["assets/favicon.svg"] = favicon(config)
    if site.social_image:
        files[site.social_image] = brand.social_image.read_bytes()  # type: ignore[union-attr]
    result = PublishResult(out=target, groups=len(site.groups), people=len(site.people))
    result.pages = sum(1 for name in files if name.endswith(".html"))
    result.problems = check_site(files, config)
    empty = [p["name"] for p in site.people if not p["publications"]]
    if empty:
        result.notes.append(
            f"{len(empty)} public {'profile has' if len(empty) == 1 else 'profiles have'} no "
            f"confirmed publications yet (only confirmed papers are published): "
            + ", ".join(empty[:5])
            + (" and others" if len(empty) > 5 else "")
        )
    if result.problems or check_only:
        return result
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".tessera-publish-", dir=target.parent))
    try:
        for name, content in files.items():
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")
        previous = target.with_name(f".{target.name}-previous")
        if previous.exists():
            shutil.rmtree(previous)
        if target.exists():
            target.rename(previous)
        staging.rename(target)
        if previous.exists():
            shutil.rmtree(previous)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    result.written = True
    return result


__all__ = ["PublishResult", "out_dir", "publish"]
