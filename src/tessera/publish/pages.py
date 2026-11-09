"""The public site's pages: home, groups, people, and the files around them."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from tessera.config import Config
from tessera.profiles import format as profile_format
from tessera.publish.projection import Site
from tessera.publish.render import (
    Page,
    area_list,
    bars,
    chips,
    citations,
    esc,
    external,
    grant_item,
    link,
    paras,
    section,
    study_item,
)
from tessera.publish.render import url as url_ok

SIDE = {"topics", "connections", "contact"}


def build(site: Site, config: Config) -> dict[str, str]:
    base = config.publish.base_url
    files: dict[str, str] = {}
    files["index.html"] = home(site, base)
    files["groups/index.html"] = groups_index(site, base)
    files["people/index.html"] = people_index(site, base)
    for g in site.groups:
        files[f"groups/{g['slug']}/index.html"] = group_page(site, base, g)
    for p in site.people:
        files[f"people/{p['slug']}/index.html"] = person_page(site, base, p)
    # Served for any missing address, so its links start from the site's root.
    root = f"{base.rstrip('/')}/" if base else "/"
    files["404.html"] = Page(
        site, base, "404.html", "Page not found", "This page does not exist.", root=root
    ).wrap(
        "<h1>Page not found</h1><p>This page does not exist, or it is no longer published. "
        f'<a href="{esc(root)}index.html">Go to the home page</a>.</p>'
    )
    files["robots.txt"] = "User-agent: *\nAllow: /\n" + (
        f"Sitemap: {base.rstrip('/')}/sitemap.xml\n" if base else ""
    )
    from tessera.publish.embed import embed_pages  # embeds reuse this module's blocks

    files.update(embed_pages(site, base))
    if base:
        files["sitemap.xml"] = sitemap(base, files)
    return files


def sitemap(base: str, files: dict[str, str]) -> str:
    urls = sorted(
        f"{base.rstrip('/')}/{name.removesuffix('index.html')}"
        for name in files
        if name.endswith("index.html")
    )
    body = "".join(f"<url><loc>{esc(u)}</loc></url>" for u in urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>\n'
    )


def _by_kind(site: Site) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for g in site.groups:
        grouped[g["kind"]].append(g)
    labels = dict(site.kinds)
    order = [k for k, _ in site.kinds] + sorted(set(grouped) - set(labels))
    return [(labels.get(k, k), grouped[k]) for k in order if grouped.get(k)]


def _group_card(page: Page, g: dict[str, Any]) -> str:
    leaders = ", ".join(p["name"] for p in g["leaders"])
    return (
        f'<li class="card"><h3>{link(page.href(f"groups/{g['slug']}/index.html"), g["name"])}</h3>'
        + (f"<p>{esc(g['description'])}</p>" if g["description"] else "")
        + f'<p class="meta">{esc(g["kind_label"])} · {len(g["people"])} '
        f"{'member' if len(g['people']) == 1 else 'members'}"
        + (f" · Led by {esc(leaders)}" if leaders else "")
        + "</p></li>"
    )


def home(site: Site, base: str) -> str:
    page = Page(site, base, "index.html", site.title,
                site.intro or f"The groups and people of {site.organization}.",
                data={"@context": "https://schema.org", "@type": "ResearchOrganization",
                      "name": site.organization})  # fmt: skip
    sections = "".join(
        f'<section class="block"><h2>{esc(label)}</h2>'
        f'<ul class="cards">{"".join(_group_card(page, g) for g in groups)}</ul></section>'
        for label, groups in _by_kind(site)
    )
    people = (
        f'<p class="lede">{len(site.people)} people with public profiles. '
        f"{link(page.href('people/index.html'), 'See them all')}.</p>"
        if site.people
        else ""
    )
    intro = f'<p class="lede">{esc(site.intro)}</p>' if site.intro else ""
    return page.wrap(
        f'<header class="page-head"><h1>{esc(site.title)}</h1>{intro}{people}</header>'
        + (sections or "<p>No groups are published yet.</p>")
    )


def groups_index(site: Site, base: str) -> str:
    page = Page(site, base, "groups/index.html", "Groups",
                f"The divisions, programs, labs, and other groups of {site.organization}.")  # fmt: skip
    body = "".join(
        f'<section class="block"><h2>{esc(label)}</h2><ul class="plain">'
        + "".join(
            f"<li>{link(page.href(f'groups/{g["slug"]}/index.html'), g['name'])}"
            + (f' <span class="meta">{esc(g["short_name"])}</span>' if g["short_name"] else "")
            + "</li>"
            for g in groups
        )
        + "</ul></section>"
        for label, groups in _by_kind(site)
    )
    return page.wrap(
        f'<header class="page-head"><h1>Groups</h1></header>{body or "<p>None yet.</p>"}'
    )


def people_index(site: Site, base: str) -> str:
    page = Page(site, base, "people/index.html", "People",
                f"People of {site.organization} with public profiles.")  # fmt: skip
    letters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in site.people:
        letters[(p["sort"][:1] or "?").upper()].append(p)
    body = "".join(
        f'<section class="block"><h2>{esc(letter)}</h2><ul class="plain people">'
        + "".join(
            f"<li>{link(page.href(f'people/{p["slug"]}/index.html'), p['name'])}"
            + (
                f' <span class="meta">{esc(", ".join(m["group"] for m in p["groups"]))}</span>'
                if p["groups"]
                else ""
            )
            + "</li>"
            for p in people
        )
        + "</ul></section>"
        for letter, people in sorted(letters.items())
    )
    return page.wrap(
        f'<header class="page-head"><h1>People</h1></header>{body or "<p>None yet.</p>"}'
    )


def _person_link(page: Page, p: dict[str, Any]) -> str:
    return (
        link(page.href(f"people/{p['slug']}/index.html"), p["name"])
        if p.get("slug")
        else esc(p["name"])
    )


def group_page(site: Site, base: str, g: dict[str, Any]) -> str:
    path = f"groups/{g['slug']}/index.html"
    data = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": g["name"],
        "parentOrganization": [{"@type": "Organization", "name": p["name"]} for p in g["parents"]]
        or {"@type": "Organization", "name": site.organization},
    }
    if g["website"]:
        data["url"] = g["website"]
    if g["email"]:
        data["email"] = g["email"]
    page = Page(site, base, path, g["name"],
                g["description"] or f"{g['kind_label']} at {site.organization}.", data=data)  # fmt: skip
    eyebrow = esc(g["kind_label"])
    if g["parents"]:
        eyebrow += " within " + ", ".join(
            link(page.href(f"groups/{p['slug']}/index.html"), p["name"]) for p in g["parents"]
        )
    contact = " ".join(
        x
        for x in (
            external(g["website"], "Website") if url_ok(g["website"]) else "",
            link(f"mailto:{g['email']}", g["email"]) if g["email"] else "",
        )
        if x
    )
    head = (
        f'<header class="page-head"><p class="eyebrow">{eyebrow}</p><h1>{esc(g["name"])}</h1>'
        + (f'<p class="lede">{esc(g["description"])}</p>' if g["description"] else "")
        + (f'<p class="contact">{contact}</p>' if contact else "")
        + "</header>"
    )
    main, side = [], []
    for block in g["blocks"]:
        html = _block(page, g, block)
        if html:
            (side if block["type"] in SIDE else main).append(html)
    if g["children"]:
        side.append(section("Within this group", '<ul class="plain">' + "".join(
            f'<li>{link(page.href(f"groups/{c["slug"]}/index.html"), c["name"])} '
            f'<span class="meta">{esc(c["kind"])}</span></li>' for c in g["children"]) + "</ul>"))  # fmt: skip
    credits = []
    if g["topics"] and site.mesh_year:
        credits.append(
            f"Topics: MeSH {site.mesh_year}, from the U.S. National Library of Medicine."
        )
    return page.wrap(
        head + f'<div class="columns"><div class="main">{"".join(main)}</div>'
        f'<aside class="side" aria-label="More about {esc(g["name"])}">{"".join(side)}</aside></div>',
        credits,
    )


def _summary(summary: dict[str, Any] | None, title: str | None = None) -> str:
    """An approved plain-language summary, labeled as such; present only when the site was built
    with its feature on."""
    if not summary:
        return ""
    note = f"{summary['label']}; approved by {summary['approved_by']}" + (
        f" on {summary['approved_on']}." if summary.get("approved_on") else "."
    )
    return section(title or "Summary", paras(summary["text"]) + f'<p class="meta">{esc(note)}</p>',
                   cls="summary")  # fmt: skip


def _block(page: Page, g: dict[str, Any], b: dict[str, Any]) -> str:
    kind = b["type"]
    title = b.get("title")
    if kind == "summary":
        return _summary(g.get("summary"), title)
    if kind == "about":
        return section(title or "About", paras(b.get("text")))
    if kind == "people":
        leaders = "".join(
            f'<li><strong>{_person_link(page, p)}</strong> <span class="meta">'
            f"{esc(', '.join(x for x in (p['role'], p['title']) if x))}</span></li>"
            for p in g["leaders"]
        )
        members = "".join(
            f'<li>{_person_link(page, p)} <span class="meta">'
            f"{esc(', '.join(x for x in (p['role'], p['title']) if x))}</span></li>"
            for p in g["people"]
            if not p["leader"]
        )
        alumni = ""
        if g["alumni"] and b.get("show_alumni") is not False:
            alumni = (
                f'<details><summary>Alumni ({len(g["alumni"])})</summary><ul class="plain">'
                + "".join(
                    f'<li>{_person_link(page, p)} <span class="meta">'
                    f"{esc(' – '.join(x for x in (p['start'], p['end']) if x))}</span></li>"
                    for p in g["alumni"]
                )
                + "</ul></details>"
            )
        body = (
            (f'<h3>{"Leaders" if len(g["leaders"]) > 1 else "Leader"}</h3><ul class="plain">{leaders}</ul>'
             if leaders else "")
            + (f'<h3>Members</h3><ul class="plain grid">{members}</ul>' if members else "")
            + alumni
        )  # fmt: skip
        return section(title or "People", body)
    if kind == "publications":
        featured = [g["featured"][p] for p in b.get("featured", []) if g["featured"].get(p)]
        limit = b.get("limit") or 50
        chart = bars(g["by_year"], "Publications by year") if b.get("show_chart", True) else ""
        out = section("Featured publications", citations(featured)) if featured else ""
        return out + section(title or "Publications", chart + citations(g["publications"][:limit]))
    if kind == "grants":
        items = g["grants"][: b.get("limit") or 12]
        return section(
            title or "Grants",
            f'<ul class="plain records">{"".join(grant_item(x) for x in items)}</ul>'
            if items
            else "",
        )
    if kind == "studies":
        items = g["studies"][: b.get("limit") or 12]
        return section(
            title or "Studies",
            f'<ul class="plain records">{"".join(study_item(x) for x in items)}</ul>'
            if items
            else "",
        )
    if kind == "topics":
        return section(title or "Topics", area_list(g["areas"]) + chips(g["topics"]))
    if kind == "connections":
        if not g["connections"]:
            return ""
        return section(title or "Writes most with", '<ul class="plain">' + "".join(
            f'<li>{link(page.href(f"groups/{c["slug"]}/index.html"), c["name"])} '
            f'<span class="meta">{c["papers"]} {"paper" if c["papers"] == 1 else "papers"}</span></li>'
            for c in g["connections"]) + "</ul>")  # fmt: skip
    if kind == "contact":
        lines = [
            external(g["website"], g["website"].removeprefix("https://").removeprefix("http://"))
            if g["website"] else "",
            link(f"mailto:{g['email']}", g["email"]) if g["email"] else "",
        ]  # fmt: skip
        body = paras(b.get("text")) + "".join(f"<p>{x}</p>" for x in lines if x)
        return section(title or "Contact", body if (b.get("text") or any(lines)) else "")
    if kind == "news":
        items = "".join(
            '<li>' + (f'<time datetime="{esc(n["date"])}">{esc(n["date"])}</time> ' if n.get("date") else "")
            + f'<strong>{external(n.get("url"), n["title"]) if n.get("url") else esc(n["title"])}</strong>'
            + (f'<p>{esc(n["text"])}</p>' if n.get("text") else "") + "</li>"
            for n in b.get("items", [])
        )  # fmt: skip
        return section(title or "News", f'<ul class="plain news">{items}</ul>' if items else "")
    if kind == "themes":
        if not g["themes"]:
            return ""
        out = []
        for t in g["themes"]:
            links = []
            for x in t["links"]:
                if x["kind"] == "person":
                    links.append(_person_link(page, {"name": x["label"], "slug": x.get("slug")}))
                elif x["kind"] == "publication":
                    links.append(
                        external(f"https://pubmed.ncbi.nlm.nih.gov/{x['pmid']}/", x["label"])
                    )
                elif x["kind"] == "study":
                    links.append(
                        external(f"https://clinicaltrials.gov/study/{x['nct_id']}", x["label"])
                    )
                else:
                    links.append(esc(x["label"]))
            out.append(
                f'<li><h3>{esc(t["name"])} <span class="tag">{esc(t["kind"])}</span></h3>'
                + paras(t["summary"])
                + (
                    f'<ul class="links">{"".join(f"<li>{x}</li>" for x in links)}</ul>'
                    if links
                    else ""
                )
                + "</li>"
            )
        return section(
            title or "Themes and projects", f'<ul class="plain themes">{"".join(out)}</ul>'
        )
    if kind == "openings":
        if not g["openings"]:
            return ""
        items = "".join(
            f'<li><h3>{esc(o["title"])} <span class="tag">{esc(o["kind_label"])}</span></h3>'
            f'<p class="meta">Closes after {esc(o["closes_on"])}</p>{paras(o["description"])}'
            + (f'<p>{external(o["url"], "How to apply", "How to apply: " + o["title"])}</p>' if o.get("url") else "")
            + (f'<p>Contact: {link("mailto:" + o["contact"], o["contact"])}</p>' if o.get("contact") else "")
            + "</li>"
            for o in g["openings"]
        )  # fmt: skip
        return section(title or "Openings", f'<ul class="plain openings">{items}</ul>')
    return ""


# The profile sections a researcher keeps, as their public page shows them (links go beside).
MAIN_PROFILE = ("overview", "interests", "position", "education", "honor", "membership",
                "teaching", "talk", "featured")  # fmt: skip


def _profile_entry(section: str, f: dict[str, Any]) -> str:
    title, meta, dates = profile_format.lines(section, f)
    note = f.get("description")
    return (
        f"<li><strong>{esc(title)}</strong>"
        + (f'<br><span class="meta">{esc(meta)}</span>' if meta else "")
        + (f'<br><span class="meta">{esc(dates)}</span>' if dates else "")
        + (paras(note) if note else "")
        + "</li>"
    )


def _profile_section(s: dict[str, Any]) -> str:
    kind, items = s["section"], s["items"]
    if kind in ("overview", "interests"):
        return section(s["label"], paras(items[0]["text"]))
    if kind == "link":
        return section(s["label"], '<ul class="plain">' + "".join(
            f"<li>{external(i['url'], i['label'])}</li>" for i in items) + "</ul>")  # fmt: skip
    if kind == "featured":
        return section(s["label"], citations([i["citation"] for i in items]))
    return section(s["label"], '<ul class="plain records">' + "".join(
        _profile_entry(kind, i) for i in items) + "</ul>")  # fmt: skip


def person_page(site: Site, base: str, p: dict[str, Any]) -> str:
    path = f"people/{p['slug']}/index.html"
    data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": p["name"],
        "affiliation": {"@type": "Organization", "name": site.organization},
    }
    if p["orcid"]:
        data["sameAs"] = f"https://orcid.org/{p['orcid']}"
    groups = ", ".join(m["group"] for m in p["groups"])
    page = Page(site, base, path, p["name"],
                f"{p['name']}, {groups or site.organization}: publications, grants, and topics.",
                kind="profile", data=data)  # fmt: skip
    memberships = "".join(
        f'<li>{link(page.href(f"groups/{m['slug']}/index.html"), m["group"])} <span class="meta">'
        f"{esc(', '.join(x for x in (m['kind'], m['role'], m['title']) if x))}</span></li>"
        for m in p["groups"]
    )
    orcid = (
        f'<p class="contact">{external(f"https://orcid.org/{p['orcid']}", "ORCID " + p["orcid"])}</p>'
        if p["orcid"] else ""
    )  # fmt: skip
    head = (
        f'<header class="page-head"><p class="eyebrow">Person</p><h1>{esc(p["name"])}</h1>'
        + (f'<p class="lede">{esc(groups)}</p>' if groups else "")
        + orcid
        + "</header>"
    )
    hidden = p.get("hidden", set())
    chosen = {s["section"]: s for s in p.get("profile", [])}
    main = (
        _summary(p.get("summary"))
        + "".join(_profile_section(chosen[k]) for k in MAIN_PROFILE if k in chosen)
        + ("" if "publications" in hidden else section("Publications", citations(p["publications"])))
        + ("" if "grants" in hidden or not p["grants"] else section("Grants", f'<ul class="plain records">{"".join(grant_item(x) for x in p["grants"])}</ul>'))
        + ("" if "studies" in hidden or not p["studies"] else section("Studies", f'<ul class="plain records">{"".join(study_item(x) for x in p["studies"])}</ul>'))
    )  # fmt: skip
    side = (
        ("" if "groups" in hidden else section("Groups", f'<ul class="plain">{memberships}</ul>' if memberships else ""))
        + (_profile_section(chosen["link"]) if "link" in chosen else "")
        + ("" if "topics" in hidden else section("Topics", area_list(p["areas"]) + chips(p["topics"])))
    )  # fmt: skip
    if "topics" in hidden:
        p = {**p, "topics": []}
    credits = []
    if p["topics"] and site.mesh_year:
        credits.append(
            f"Topics: MeSH {site.mesh_year}, from the U.S. National Library of Medicine."
        )
    return page.wrap(
        head
        + f'<div class="columns"><div class="main">{main or "<p>Nothing to show yet.</p>"}</div>'
        f'<aside class="side" aria-label="More about {esc(p["name"])}">{side}</aside></div>',
        credits,
    )


__all__ = ["build"]
