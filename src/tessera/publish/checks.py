"""Checks every page of the public site before it replaces the published one.

Each page must: declare its language; have one non-empty title and one ``h1``; keep heading
levels in order (no jump from ``h2`` to ``h4``); have its landmarks (a header, a labelled
navigation, one ``main``, a footer); give every link and image a text alternative; carry no
script other than structured data, no event handlers, and no ``javascript:`` links; and link
only to pages that exist in the site, or to http(s) and mail addresses. The colors must meet
WCAG AA in both modes (see tessera.branding).
"""

from __future__ import annotations

import posixpath
from html.parser import HTMLParser
from typing import Any

from tessera.branding import contrast_problems
from tessera.config import Config

VOID = {"meta", "link", "img", "br", "hr", "input", "source", "rect", "path", "circle"}


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lang: str | None = None
        self.titles: list[str] = []
        self.headings: list[int] = []
        self.landmarks: dict[str, int] = {}
        self.nav_labels: list[str | None] = []
        self.links: list[dict[str, Any]] = []
        self.problems: list[str] = []
        self._title: list[str] | None = None
        self._link: dict[str, Any] | None = None
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        for name, value in attrs:
            if name.startswith("on"):
                self.problems.append(f"<{tag}> has an event handler ({name})")
            if name in ("href", "src") and (value or "").strip().lower().startswith("javascript:"):
                self.problems.append(f"<{tag}> links to javascript:")
            if name == "style":
                self.problems.append(f"<{tag}> has an inline style")
        if tag == "html":
            self.lang = a.get("lang")
        elif tag == "title":
            self._title = []
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.headings.append(int(tag[1]))
        elif tag in ("header", "main", "footer", "nav"):
            self.landmarks[tag] = self.landmarks.get(tag, 0) + 1
            if tag == "nav":
                self.nav_labels.append(a.get("aria-label"))
        elif tag == "a":
            self._link = {"href": a.get("href"), "label": a.get("aria-label"), "text": []}
        elif tag == "img" and not a.get("alt") and a.get("alt") != "":
            self.problems.append("an image has no alt text")
        elif tag == "svg" and a.get("role") == "img" and not a.get("aria-label"):
            self.problems.append("a chart has no text alternative")
        elif tag == "script" and a.get("type") != "application/ld+json":
            self.problems.append("a page carries a script")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._title is not None:
            self.titles.append("".join(self._title).strip())
            self._title = None
        elif tag == "a" and self._link is not None:
            self._link["text"] = "".join(self._link["text"]).strip()
            self.links.append(self._link)
            self._link = None

    def handle_data(self, data: str) -> None:
        if self._title is not None:
            self._title.append(data)
        if self._link is not None:
            self._link["text"].append(data)


def check_page(name: str, html: str, files: set[str]) -> list[str]:
    page = _Page()
    page.feed(html)
    page.close()
    problems = list(page.problems)
    if not page.lang:
        problems.append("no language on <html>")
    if len(page.titles) != 1 or not page.titles[0]:
        problems.append("a page needs exactly one non-empty <title>")
    if page.headings.count(1) != 1:
        problems.append(f"a page needs exactly one <h1> (found {page.headings.count(1)})")
    previous = 0
    for level in page.headings:
        if level > previous + 1:
            problems.append(f"heading levels jump from h{previous} to h{level}")
            break
        previous = level
    for landmark in ("header", "main", "footer", "nav"):
        if not page.landmarks.get(landmark):
            problems.append(f"no <{landmark}> landmark")
    if page.landmarks.get("main", 0) > 1:
        problems.append("more than one <main>")
    if any(not label for label in page.nav_labels):
        problems.append("a <nav> has no label")
    here = posixpath.dirname(name)
    for link in page.links:
        href = link["href"] or ""
        if not (link["text"] or link["label"]):
            problems.append(f"a link to {href or '(nowhere)'} has no text")
        if not href:
            problems.append("a link has no address")
        elif href.startswith(("http://", "https://", "mailto:", "#")):
            continue
        elif ":" in href.split("/")[0]:
            problems.append(f"a link uses an unsupported scheme: {href}")
        elif not href.startswith("/"):
            target = posixpath.normpath(posixpath.join(here, href.split("#")[0]))
            if target not in files:
                problems.append(f"a link points to a missing page: {href}")
    return [f"{name}: {p}" for p in problems]


def check_site(files: dict[str, Any], config: Config) -> list[str]:
    names = set(files)
    problems: list[str] = []
    for name, content in sorted(files.items()):
        if name.endswith(".html") and name != "404.html" and isinstance(content, str):
            problems += check_page(name, content, names)
    problems += [f"colors: {p}" for p in contrast_problems(config)]
    return problems


__all__ = ["check_page", "check_site"]
