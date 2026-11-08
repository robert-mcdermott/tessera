"""Group content to embed in another website (LAB-09).

For every public group, ``tessera publish`` also writes:

* ``groups/<slug>/embed/<part>.html`` for its publications, people, openings, and news: small
  pages made to be framed (an ``<iframe>``), from the same allowlisted projection as the rest
  of the site. They have no site header, carry no scripts, open links in the page around them,
  and link back to the group's full page. ``<part>.html`` follows the reader's light or dark
  preference; ``<part>-light.html`` and ``<part>-dark.html`` are held to one, to match the
  page around them;
* ``groups/<slug>/feed.json``: a JSON Feed (version 1.1) of the group's news, open openings,
  and confirmed publications, for sites that render content themselves.

An embed always exists for a public group, even when it has nothing to show yet, so that the
code on another site keeps working; a group that is not public has none.
"""

from __future__ import annotations

import json
from typing import Any

from tessera.publish.pages import _block
from tessera.publish.projection import Site
from tessera.publish.render import Page, esc, link

PARTS = {
    "publications": "Publications",
    "people": "People",
    "openings": "Openings",
    "news": "News",
}
EMPTY = {
    "publications": "No confirmed publications yet.",
    "people": "No members are listed yet.",
    "openings": "No openings right now.",
    "news": "No news yet.",
}
FEED_LIMIT = 50


THEMES = {"": "light dark", "-light": "light", "-dark": "dark"}  # file suffix: color scheme


class EmbedPage(Page):
    """A page without the site's header and navigation, made to sit inside another page. It
    follows the reader's light or dark preference, or is held to one (``theme``), so that it
    can match the page around it."""

    theme = ""

    def wrap(self, body: str, credits: Any = ()) -> str:  # type: ignore[override]
        site = self.site
        canonical = f'<link rel="canonical" href="{esc(self.canonical)}">' if self.canonical else ""
        return f"""<!doctype html>
<html lang="en"{f' data-theme="{self.theme[1:]}"' if self.theme else ""}>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="{THEMES[self.theme]}">
<meta name="robots" content="noindex">
<title>{esc(self.title)} · {esc(site.title)}</title>
<meta name="description" content="{esc(self.description)}">
{canonical}
<base target="_top">
<link rel="stylesheet" href="{self.href("assets/site.css")}">
</head>
<body class="embed">
<main id="main">
{body}
</main>
<footer class="embed-footer"><p>From {link(self.href("index.html"), site.title)}</p></footer>
</body>
</html>
"""


def _part(site: Site, base: str, g: dict[str, Any], part: str, theme: str = "") -> str:
    path = f"groups/{g['slug']}/embed/{part}{theme}.html"
    label = PARTS[part]
    page = EmbedPage(site, base, path, f"{label} · {g['name']}",
                     f"{label} of {g['name']}, from {site.title}.")  # fmt: skip
    page.theme = theme
    block = next((b for b in g["blocks"] if b["type"] == part), {"type": part})
    if part == "publications":
        block = {**block, "show_chart": False}
    if part == "people":
        block = {**block, "show_alumni": False}
    content = _block(page, g, {**block, "title": label})
    full = page.href(f"groups/{g['slug']}/index.html")
    head = (
        f'<h1 class="embed-title">{link(full, g["name"])}</h1>'
        if content
        else f'<h1 class="embed-title">{link(full, g["name"])}</h1><h2>{esc(label)}</h2>'
        f'<p class="meta">{esc(EMPTY[part])}</p>'
    )
    return page.wrap(head + content)


def embed_pages(site: Site, base: str) -> dict[str, str]:
    files = {}
    for g in site.groups:
        for part in PARTS:
            for theme in THEMES:
                files[f"groups/{g['slug']}/embed/{part}{theme}.html"] = _part(
                    site, base, g, part, theme
                )
        files[f"groups/{g['slug']}/feed.json"] = feed(site, base, g)
    return files


def _when(day: str | None) -> str | None:
    return f"{day}T00:00:00Z" if day else None


def feed(site: Site, base: str, g: dict[str, Any]) -> str:
    """A JSON Feed 1.1 of the group's news, open openings, and confirmed publications."""
    root = base.rstrip("/")
    home = f"{root}/groups/{g['slug']}/" if root else None
    items: list[dict[str, Any]] = []
    for block in g["blocks"]:
        if block["type"] != "news":
            continue
        for n, item in enumerate(block.get("items", []), start=1):
            entry: dict[str, Any] = {
                "id": f"{home or g['slug'] + '/'}#news-{block['id']}-{n}",
                "title": item["title"],
                "content_text": item.get("text") or item["title"],
                "tags": ["news"],
            }
            if item.get("url"):
                entry["external_url"] = item["url"]
            if item.get("date"):
                entry["date_published"] = _when(item["date"])
            items.append(entry)
    for o in g["openings"]:
        entry = {
            "id": f"{home or g['slug'] + '/'}#opening-{o['title']}",
            "title": o["title"],
            "content_text": (o.get("description") or o["title"])
            + f"\n\nCloses after {o['closes_on']}.",
            "tags": ["opening", o["kind_label"].lower()],
        }
        if o.get("url"):
            entry["external_url"] = o["url"]
        items.append(entry)
    for c in g["publications"][:FEED_LIMIT]:
        venue = ". ".join(str(x) for x in (c.get("journal"), c.get("year")) if x)
        entry = {
            "id": f"https://pubmed.ncbi.nlm.nih.gov/{c['pmid']}/",
            "url": f"https://doi.org/{c['doi']}" if c.get("doi")
            else f"https://pubmed.ncbi.nlm.nih.gov/{c['pmid']}/",
            "title": c.get("title") or f"PubMed {c['pmid']}",
            "content_text": ". ".join(x for x in (c.get("authors"), c.get("title"), venue) if x),
            "tags": ["publication"],
        }  # fmt: skip
        items.append(entry)
    body: dict[str, Any] = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": f"{g['name']} · {site.title}",
        "description": g["description"] or f"{g['kind_label']} at {site.organization}.",
        "language": "en",
        "items": items,
    }
    if home:
        body["home_page_url"] = home
        body["feed_url"] = f"{home}feed.json"
    return json.dumps(body, indent=2, ensure_ascii=False) + "\n"


__all__ = ["PARTS", "THEMES", "EmbedPage", "embed_pages", "feed"]
