"""The demo organization: invented, deterministic, and complete enough for every screen."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tessera.api.app import create_app
from tessera.config import load_config
from tessera.demo import DemoError, build_demo
from tessera.demo.world import build_world

TODAY = date(2026, 11, 7)


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[dict, TestClient]]:
    path = tmp_path_factory.mktemp("demo")
    result = build_demo(path, today=TODAY)
    config = load_config(Path(result["config"]), env={})
    with TestClient(create_app(config), base_url="http://localhost") as client:
        yield result, client


def test_identifiers_can_never_be_real_ones() -> None:
    world = build_world(today=TODAY)
    assert all(int(r["pmid"]) >= 900_000_000 for r in world.pubmed)
    assert all(r["doi"].startswith("10.5555/") for r in world.pubmed)
    assert all(s["nct_id"].startswith("NCT9") for s in world.studies)
    assert all(int(r["core_project_num"][-6:]) >= 990_000 for r in world.reporter)
    orcids = {p.orcid for p in world.people if p.orcid}
    assert orcids and all(o.startswith("0000-0000-") for o in orcids)
    assert all(p.email.endswith("@corvane.example") for p in world.people if p.email)
    headings = {m["descriptor_ui"] for r in world.pubmed for m in r["mesh"]}
    assert all(re.fullmatch(r"D9\d{5}", ui) for ui in headings)


def test_the_same_seed_builds_the_same_organization() -> None:
    one, two = build_world(seed=3, today=TODAY), build_world(seed=3, today=TODAY)
    assert [r["title"] for r in one.pubmed] == [r["title"] for r in two.pubmed]
    assert [p.name for p in one.people] == [p.name for p in two.people]
    assert [r["title"] for r in build_world(seed=4, today=TODAY).pubmed] != [
        r["title"] for r in one.pubmed
    ]


def test_the_demo_fills_every_screen(demo) -> None:  # type: ignore[no-untyped-def]
    result, client = demo
    assert result["people"] >= 80 and result["papers"] >= 500
    assert result["accepted"] > 500 and result["rejected"] >= 20 and result["merged"] >= 10

    def data(path: str, **params):  # type: ignore[no-untyped-def]
        response = client.get(path, params=params)
        assert response.status_code == 200, (path, response.text)
        return response.json()

    sources = data("/api/v1/sources")["data"]["sources"]
    assert all(not s["stale"] for s in sources if s["name"] != "icite" or s["enabled"])
    assert data("/api/v1/review")["meta"]["total"] > 0  # something left to review
    assert data("/api/v1/duplicates")["data"]  # pairs left for a curator
    assert data("/api/v1/candidates")["data"]  # discovered people waiting
    assert data("/api/v1/evidence")["data"]["evaluation"]["decisions"]["negatives"] >= 20
    experts = data("/api/v1/experts", q="memory T cells")
    assert experts["meta"]["ready"] and experts["data"]
    network = data("/api/v1/graph/network")
    assert network["meta"]["groups"] and len(network["data"]["nodes"]) >= 50
    assert len(data("/api/v1/graph/units")["data"]["units"]) >= 4
    assert data("/api/v1/graph/missed", min_similarity=0.1)["meta"]["total"] > 0
    assert len(data("/api/v1/accounts")["data"]) >= 4


def test_an_existing_demo_is_kept_unless_forced(demo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    build_demo(tmp_path, today=TODAY, labs_per_area=1)
    with pytest.raises(DemoError, match="exists"):
        build_demo(tmp_path, today=TODAY, labs_per_area=1)
    build_demo(tmp_path, today=TODAY, labs_per_area=1, force=True)


def test_mesh_synonyms_broader_headings_and_areas(demo) -> None:  # type: ignore[no-untyped-def]
    _, client = demo

    def data(path: str, **params):  # type: ignore[no-untyped-def]
        response = client.get(path, params=params)
        assert response.status_code == 200, (path, response.text)
        return response.json()

    cancer = data("/api/v1/experts", q="cancer")
    first = cancer["meta"]["concepts"][0]
    assert (first["descriptor"], first.get("via")) == ("Neoplasms", "cancer")
    assert cancer["meta"]["mesh_year"] == TODAY.year
    # Nobody holds "Neoplasms" itself: people are found through the narrower headings.
    narrower = {m["descriptor"] for p in cancer["data"] for m in p["matches"]}
    assert narrower & {"Breast Neoplasms", "Colorectal Neoplasms", "Multiple Myeloma", "Melanoma"}
    aml = data("/api/v1/experts", q="AML")["meta"]["concepts"][0]
    assert aml["descriptor"] == "Leukemia, Myeloid, Acute"
    assert data("/api/v1/concepts", q="tumor")["data"][0]["descriptor"] == "Neoplasms"

    person = data("/api/v1/graph/network")["data"]["nodes"][0]["id"]
    areas = data(f"/api/v1/people/{person}/concepts")["meta"]["areas"]
    # Shares of the whole profile; at most eight areas are kept, so they can sum below one.
    assert areas and len(areas) <= 8 and 0.5 <= sum(a["share"] for a in areas) <= 1.01
    assert all(a["category"] in "ABCFG" for a in areas)  # what someone studies, not methods
    landscape = data("/api/v1/graph/landscape")["data"]["nodes"]
    assert landscape and all(n["category"] for n in landscape)


def test_groups_pages_and_attribution(demo) -> None:  # type: ignore[no-untyped-def]
    _, client = demo
    listing = client.get("/api/v1/groups", params={"limit": 200}).json()
    kinds = {k["id"]: k["count"] for k in listing["meta"]["kinds"]}
    assert kinds["division"] == 5 and kinds["program"] == 3 and kinds["core"] == 1
    assert kinds["lab"] >= 20
    labs = [g for g in listing["data"] if g["kind"] == "lab"]
    assert all(g["leaders"] and g["parents"] for g in labs)
    program = next(g for g in listing["data"] if g["slug"] == "cancer-immunotherapy-program")
    assert len(program["parents"]) == 2  # a program spans two divisions

    lab = labs[0]
    overview = client.get(f"/api/v1/groups/{lab['slug']}/overview").json()["data"]
    assert overview["leaders"][0]["role"] == "lead"
    assert overview["topics"] and overview["people"]
    assert overview["publications"]["accepted"] > 0
    assert all(c["kind"] in ("lab", "program", "core") for c in overview["connections"])
    papers = client.get(f"/api/v1/groups/{lab['slug']}/publications").json()
    assert papers["meta"]["total"] == overview["publications"]["accepted"]
    assert papers["data"][0]["summary"]["title"]

    # Suggestions carry their evidence, and a paper is the group's only once someone says so.
    suggested = client.get(
        "/api/v1/groups/cancer-immunotherapy-program/publications", params={"status": "suggested"}
    ).json()["data"]
    assert suggested and all(
        s["assertion"]["status"] == "suggested" and s["assertion"]["evidence"] for s in suggested
    )
    one = suggested[0]["assertion"]
    features = {e["feature"] for e in one["evidence"]}
    assert "member_authors" in features
    decided = client.post(
        "/api/v1/groups/cancer-immunotherapy-program/publications/decisions",
        json={"ids": [one["id"]], "action": "accept"},
    )
    assert decided.status_code == 200, decided.text
    alumni = [g for g in labs if client.get(f"/api/v1/groups/{g['slug']}/overview").json()[
        "data"]["alumni"]]  # fmt: skip
    assert alumni, "the demo has alumni"
    person = overview["leaders"][0]["person_id"]
    profile = client.get(f"/api/v1/people/{person}").json()["data"]
    assert any(u["group_slug"] == lab["slug"] and u["role"] == "lead" for u in profile["units"])


def test_a_leaders_authorship_alone_is_weak_evidence() -> None:
    from tessera.groups.attribution import _evidence

    evidence, score = _evidence(
        "900000001", [("per_pi", {"author_position": 5, "author_count": 9})],
        {"per_pi": {"lead"}}, {"lead"}, {"per_pi": "Ada Okafor"}, ["Okafor Lab"], set(), set(),
        set(), set(), {},
    )  # fmt: skip
    assert score < 0.6 and [e["feature"] for e in evidence] == ["member_authors"]
    _, two = _evidence(
        "900000001",
        [("per_pi", {"author_position": 9, "author_count": 9}),
         ("per_pd", {"author_position": 1, "author_count": 9})],
        {"per_pi": {"lead"}, "per_pd": {"trainee"}}, {"lead"}, {}, ["Okafor Lab"],
        {"grt:R01"}, {"grt:R01"}, set(), set(),
        {("900000001", 1): ["Okafor Lab, Corvane Institute"]},
    )  # fmt: skip
    assert two == pytest.approx(0.98)  # members, senior leader, affiliation, and grant: capped


def test_inbox_themes_and_openings(demo) -> None:  # type: ignore[no-untyped-def]
    _, client = demo
    overview = client.get("/api/v1/groups/cancer-immunotherapy-program/overview").json()["data"]
    assert [t["kind"] for t in overview["themes"]] == ["project", "theme"]
    assert {link["kind"] for t in overview["themes"] for link in t["links"]} >= {
        "person", "publication",
    }  # fmt: skip
    assert len(overview["openings"]) == 2 and overview["openings"][0]["days_left"] <= 14
    digest = client.get("/api/v1/inbox").json()["data"]
    program = next(g for g in digest["groups"] if g["slug"] == "cancer-immunotherapy-program")
    assert program["counts"]["openings"] == 1 and program["counts"]["papers"] >= 1
    lab = next(g for g in digest["groups"] if g["counts"]["roster"])
    items = client.get(f"/api/v1/groups/{lab['id']}/inbox").json()["data"]
    assert items["roster"][0]["action"] == "member_add" and items["papers"][0]["summary"]["title"]
    assert client.post(f"/api/v1/groups/{lab['id']}/inbox/reviewed").status_code == 200
    again = client.get(f"/api/v1/groups/{lab['id']}/inbox").json()["data"]
    assert again["counts"]["roster"] == 0


def test_the_public_site_shows_only_what_it_may(demo) -> None:  # type: ignore[no-untyped-def]
    result, _ = demo
    site = Path(result["public_site"]).parent
    pages = {p.relative_to(site).as_posix(): p.read_text("utf-8") for p in site.rglob("*.html")}
    assert "index.html" in pages and "groups/cancer-immunotherapy-program/index.html" in pages
    everything = "\n".join(pages.values())
    world = build_world(today=TODAY)
    # Citations and links, never abstracts; no one's email address; no internal IDs.
    assert not any(r["abstract"][:80] in everything for r in world.pubmed if r.get("abstract"))
    assert not any(p.email in everything for p in world.people if p.email)
    assert "per_" not in everything and "unt_" not in everything
    assert "pubmed.ncbi.nlm.nih.gov" in everything and "doi.org/10.5555" in everything
    program = pages["groups/cancer-immunotherapy-program/index.html"]
    assert "Postdoctoral fellow in cell therapy" in program  # an open opening
    assert "Engineered T cells for solid tumors" in program  # a theme
    assert '<script type="application/ld+json">' in program and "<script>" not in program
    sitemap = (site / "sitemap.xml").read_text()
    assert "<loc>https://research.corvane.example/groups/</loc>" in sitemap
    assert "/embed/" not in sitemap  # embeds are not pages to find


def test_group_content_embeds_in_other_websites(demo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    import json

    from sqlalchemy import update

    from tessera.home import DataHome
    from tessera.ledger.db import make_engine
    from tessera.ledger.tables import unit
    from tessera.publish import publish
    from tessera.publish.embed import PARTS, THEMES

    result, client = demo
    site = Path(result["public_site"]).parent
    folder = site / "groups" / "cancer-immunotherapy-program"
    for part in PARTS:
        for theme in THEMES:
            assert (folder / "embed" / f"{part}{theme}.html").is_file(), (part, theme)
    page = (folder / "embed" / "publications-light.html").read_text()
    assert '<html lang="en" data-theme="light">' in page and '<base target="_top">' in page
    assert "<nav" not in page and 'class="site-header"' not in page and "<script" not in page
    assert "pubmed.ncbi.nlm.nih.gov" in page
    assert 'href="../../../groups/cancer-immunotherapy-program/index.html"' in page  # the full page
    assert '<meta name="robots" content="noindex">' in page
    feed = json.loads((folder / "feed.json").read_text())
    assert feed["version"] == "https://jsonfeed.org/version/1.1"
    home = "https://research.corvane.example/groups/cancer-immunotherapy-program/"
    assert feed["home_page_url"] == home and feed["feed_url"] == f"{home}feed.json"
    assert feed["items"] and all(i["id"] and i["content_text"] for i in feed["items"])
    assert {"news", "opening", "publication"} <= {i["tags"][0] for i in feed["items"]}
    assert "per_" not in json.dumps(feed)
    meta = client.get("/api/v1/meta").json()["data"]
    assert meta["public_site"] == "https://research.corvane.example"

    # A group that is not public has no embeds.
    config = load_config(Path(result["config"]), env={})
    engine = make_engine(config.ledger_url)
    try:
        with engine.begin() as conn:
            conn.execute(update(unit).where(unit.c.slug == "cancer-immunotherapy-program")
                         .values(public=False))  # fmt: skip
        out = tmp_path / "site"
        quiet = publish(config, DataHome(config.data.home), engine, out=out, today=TODAY)
        assert quiet.written and not quiet.problems
        assert not (out / "groups" / "cancer-immunotherapy-program").exists()
        with engine.begin() as conn:
            conn.execute(update(unit).where(unit.c.slug == "cancer-immunotherapy-program")
                         .values(public=True))  # fmt: skip
    finally:
        engine.dispose()


def test_publishing_checks_before_replacing(demo, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    import dataclasses

    from tessera.home import DataHome
    from tessera.ledger.db import make_engine
    from tessera.publish import publish

    result, _ = demo
    config = load_config(Path(result["config"]), env={})
    engine = make_engine(config.ledger_url)
    try:
        out = tmp_path / "site"
        published = dataclasses.replace(config, publish=dataclasses.replace(
            config.publish, base_url="https://research.corvane.example"))  # fmt: skip
        good = publish(published, DataHome(config.data.home), engine, out=out, today=TODAY)
        assert good.written and not good.problems
        sitemap = (out / "sitemap.xml").read_text()
        assert "<loc>https://research.corvane.example/groups/</loc>" in sitemap
        assert 'rel="canonical" href="https://research.corvane.example/"' in (
            out / "index.html").read_text()  # fmt: skip
        # Yellow links on white fail WCAG AA: the build stops and the site stays as it was.
        before = (out / "index.html").read_text()
        garish = dataclasses.replace(published, branding=dataclasses.replace(
            config.branding, accent_color="#ffff00"))  # fmt: skip
        bad = publish(garish, DataHome(config.data.home), engine, out=out, today=TODAY)
        assert not bad.written and any("links on the page" in p for p in bad.problems)
        assert (out / "index.html").read_text() == before
    finally:
        engine.dispose()
