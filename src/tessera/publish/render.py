"""HTML for the public site: plain, accessible pages with no scripts.

Every value is escaped, every link is checked to be http(s) or a page of the site, and pages
link to each other with relative ``…/index.html`` addresses, so the site works from any web
server, any folder, and from disk.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from html import escape
from typing import Any

from tessera.publish.projection import Site

_URL = re.compile(r"^https?://[^\s<>\"']+$")


def esc(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


def url(value: str | None) -> str | None:
    return value if value and _URL.match(value) else None


def link(href: str | None, text: str, **attrs: str) -> str:
    extra = "".join(f' {k.replace("_", "-")}="{esc(v)}"' for k, v in attrs.items() if v)
    return f'<a href="{esc(href)}"{extra}>{esc(text)}</a>' if href else esc(text)


def external(href: str | None, text: str, label: str | None = None) -> str:
    return link(url(href), text, rel="noopener", aria_label=label or "")


def paragraphs(text: str) -> list[str]:
    """Blank lines separate paragraphs; single line breaks join."""
    parts = re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n"))
    return [" ".join(p.split()) for p in parts if p.strip()]


def paras(text: str | None) -> str:
    return "".join(f"<p>{esc(p)}</p>" for p in paragraphs(text or ""))


class Page:
    """One page: where it is (for relative links), its title, and its metadata."""

    def __init__(self, site: Site, base_url: str, path: str, title: str, description: str,
                 kind: str = "website", data: dict[str, Any] | None = None,
                 root: str | None = None) -> None:  # fmt: skip
        self.site = site
        self.path = path  # such as "groups/okafor-lab/index.html"
        self.title = title
        self.description = description
        self.kind = kind
        self.data = data
        self.base_url = base_url.rstrip("/")
        self.root = root if root is not None else "../" * path.count("/")

    def href(self, target: str) -> str:
        """A link to another page of the site, relative to this one."""
        return f"{self.root}{target}"

    @property
    def canonical(self) -> str | None:
        if not self.base_url:
            return None
        pretty = self.path.removesuffix("index.html")
        return f"{self.base_url}/{pretty}"

    def wrap(self, body: str, credits: Iterable[str] = ()) -> str:
        site = self.site
        full_title = self.title if self.title == site.title else f"{self.title} · {site.title}"
        meta = [
            f'<meta name="description" content="{esc(self.description)}">',
            f'<meta property="og:title" content="{esc(self.title)}">',
            f'<meta property="og:description" content="{esc(self.description)}">',
            f'<meta property="og:type" content="{esc(self.kind)}">',
            f'<meta property="og:site_name" content="{esc(site.title)}">',
        ]
        if self.canonical:
            meta.append(f'<link rel="canonical" href="{esc(self.canonical)}">')
            meta.append(f'<meta property="og:url" content="{esc(self.canonical)}">')
        if self.data:
            data = json.dumps(self.data, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
            meta.append(f'<script type="application/ld+json">{data}</script>')
        notes = "".join(f"<p>{esc(c)}</p>" for c in credits)
        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{esc(full_title)}</title>
{chr(10).join(meta)}
<link rel="stylesheet" href="{self.href("assets/site.css")}">
<link rel="icon" href="{self.href("assets/favicon.svg")}" type="image/svg+xml">
</head>
<body>
<a class="skip" href="#main">Skip to the content</a>
<header class="site-header"><div class="wrap">
<a class="brand" href="{self.href("index.html")}"><span class="brand-mark" aria-hidden="true"></span><span>{esc(site.title)}</span></a>
<nav aria-label="Site"><ul>
<li><a href="{self.href("groups/index.html")}"{' aria-current="page"' if self.path.startswith("groups/") else ""}>Groups</a></li>
<li><a href="{self.href("people/index.html")}"{' aria-current="page"' if self.path.startswith("people/") else ""}>People</a></li>
</ul></nav>
</div></header>
<main id="main" class="wrap">
{body}
</main>
<footer class="site-footer"><div class="wrap">
<p>{esc(site.organization)} · Updated {esc(site.built.isoformat())}</p>
{notes}
<p>Built from public records with Tessera.</p>
</div></footer>
</body>
</html>
"""


# --- pieces ------------------------------------------------------------------------------


def citation(c: dict[str, Any]) -> str:
    """Authors. Title. Journal. Year. With links to the DOI and PubMed, never the abstract."""
    parts = []
    if c.get("authors"):
        parts.append(f'<span class="authors">{esc(c["authors"])}.</span>')
    title = esc(c.get("title") or f"PubMed {c.get('pmid')}")
    parts.append(f'<span class="title">{title}</span>')
    venue = " ".join(
        x for x in (f"<i>{esc(c['journal'])}</i>." if c.get("journal") else "",
                    f"{esc(c['year'])}." if c.get("year") else "") if x
    )  # fmt: skip
    if venue:
        parts.append(f'<span class="venue">{venue}</span>')
    links = []
    if c.get("doi"):
        links.append(external(f"https://doi.org/{c['doi']}", "DOI", f"DOI for {c.get('title')}"))
    if c.get("pmid"):
        links.append(external(f"https://pubmed.ncbi.nlm.nih.gov/{c['pmid']}/", "PubMed",
                              f"PubMed record for {c.get('title')}"))  # fmt: skip
    flag = ' <strong class="flag">Retracted</strong>' if c.get("is_retracted") else ""
    return f'<li class="cite">{" ".join(parts)}{flag} <span class="links">{" · ".join(links)}</span></li>'


def citations(items: list[dict[str, Any]]) -> str:
    return f'<ol class="citations">{"".join(citation(c) for c in items)}</ol>' if items else ""


def grant_item(g: dict[str, Any]) -> str:
    years = g.get("fiscal_years") or []
    span = f"{years[0]}–{years[1]}" if len(years) == 2 and years[0] != years[1] else (
        str(years[0]) if years else "")  # fmt: skip
    meta = " · ".join(x for x in (g.get("core_project_num"), g.get("institute"), span,
                                  "active" if g.get("is_active") else "ended") if x)  # fmt: skip
    return f'<li><span class="title">{esc(g.get("title"))}</span> <span class="meta">{esc(meta)}</span></li>'


def study_item(s: dict[str, Any]) -> str:
    status = (s.get("registry_status") or "").replace("_", " ").lower()
    phases = ", ".join(
        p.replace("PHASE", "Phase ").replace("_", " ") for p in s.get("phases") or []
    )
    meta = " · ".join(x for x in (s.get("nct_id"), phases, status) if x)
    href = f"https://clinicaltrials.gov/study/{s['nct_id']}" if s.get("nct_id") else None
    return (f'<li>{external(href, s.get("title") or s.get("nct_id") or "Study")} '
            f'<span class="meta">{esc(meta)}</span></li>')  # fmt: skip


def section(title: str, body: str, *, cls: str = "") -> str:
    if not body:
        return ""
    return f'<section class="block {cls}"><h2>{esc(title)}</h2>{body}</section>'


def bars(rows: list[tuple[int, int]], label: str) -> str:
    """A small bar chart of counts by year, as SVG with a text alternative."""
    if len(rows) < 2:
        return ""
    top = max(n for _, n in rows) or 1
    width = len(rows) * 28
    rects = "".join(
        f'<rect x="{i * 28 + 4}" y="{60 - n / top * 52:.1f}" width="20" height="{n / top * 52:.1f}" rx="2"/>'
        f'<text x="{i * 28 + 14}" y="74" text-anchor="middle">{str(y)[2:]}</text>'
        for i, (y, n) in enumerate(rows)
    )
    summary = ", ".join(f"{y}: {n}" for y, n in rows)
    return (f'<figure class="chart"><svg viewBox="0 0 {width} 78" role="img" '
            f'aria-label="{esc(label)}: {esc(summary)}">{rects}</svg></figure>')  # fmt: skip


def chips(items: list[str]) -> str:
    return f'<ul class="chips">{"".join(f"<li>{esc(i)}</li>" for i in items)}</ul>' if items else ""


def area_list(areas: list[dict[str, Any]]) -> str:
    if not areas:
        return ""
    rows = "".join(
        f"<li><span>{esc(a['area'])}</span>"
        f'<svg class="meter" viewBox="0 0 100 6" preserveAspectRatio="none" aria-hidden="true">'
        f'<rect class="track" width="100" height="6" rx="3"/>'
        f'<rect width="{max(3, round(a["share"] * 100))}" height="6" rx="3"/></svg>'
        f'<span class="pct">{round(a["share"] * 100)}%</span></li>'
        for a in areas
    )
    return f'<ul class="areas">{rows}</ul>'


__all__ = ["Page", "area_list", "bars", "chips", "citation", "citations", "esc", "external",
           "grant_item", "link", "paras", "section", "study_item", "url"]  # fmt: skip
