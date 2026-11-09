"""The /api/v1 endpoints over a synthetic organization."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tessera.api.app import create_app
from tessera.api.common import decode_cursor, encode_cursor
from tessera.connectors.http import HttpClient
from tessera.evidence.people import import_roster
from tessera.evidence.resolve import resolve
from tessera.jobs.build import build_search
from tessera.ledger.db import make_engine
from tessera.search.query import MARK_END, MARK_START

from . import synthetic_org
from .conftest import Instance
from .fakes import no_sleep, pubmed_api


@pytest.fixture(scope="module")
def world_client(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[TestClient, synthetic_org.World]]:
    from tessera.config import example_config_text, load_config
    from tessera.home import DataHome
    from tessera.ledger import migrate

    tmp = tmp_path_factory.mktemp("api")
    (tmp / "tessera.toml").write_text(example_config_text())
    config = load_config(tmp / "tessera.toml", env={})
    home = DataHome(config.data.home)
    home.create()
    engine = make_engine(config.ledger_url)
    migrate.upgrade(engine)
    world = synthetic_org.build(tmp)
    synthetic_org.load(world, home.lake)
    import_roster(engine, world.roster_path)
    resolve(config, home, engine, today=date(2026, 11, 7))
    build_search(config, home, engine)
    engine.dispose()
    with TestClient(create_app(config), base_url="http://localhost") as client:
        yield client, world


@pytest.fixture
def client(world_client) -> TestClient:  # type: ignore[no-untyped-def]
    return world_client[0]


@pytest.fixture
def world(world_client) -> synthetic_org.World:  # type: ignore[no-untyped-def]
    return world_client[1]


def ok(response):  # type: ignore[no-untyped-def]
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"data", "meta"}
    return body


def person_id(client: TestClient, family: str) -> str:
    items = ok(client.get("/api/v1/people", params={"q": family}))["data"]
    return next(i["id"] for i in items if i["name"].endswith(family))


# --- conventions ---------------------------------------------------------------------------------


def test_cursor_roundtrip() -> None:
    assert decode_cursor(encode_cursor(40)) == 40
    assert decode_cursor(None) == 0


@pytest.mark.parametrize("cursor", ["!!!", "eyJ4IjoxfQ", encode_cursor(0)[:-2] + "zz"])
def test_bad_cursor_is_a_400(client: TestClient, cursor: str) -> None:
    response = client.get("/api/v1/people", params={"cursor": cursor})
    assert response.status_code == 400
    assert response.headers["content-type"] == "application/problem+json"


def test_paging_and_fields(client: TestClient) -> None:
    first = ok(client.get("/api/v1/people", params={"limit": 15, "fields": "id,name"}))
    assert first["meta"]["total"] == 40 and len(first["data"]) == 15
    assert set(first["data"][0]) == {"id", "name"}
    seen = [p["id"] for p in first["data"]]
    cursor = first["meta"]["next_cursor"]
    while cursor:
        page = ok(client.get("/api/v1/people", params={"limit": 15, "cursor": cursor}))
        seen += [p["id"] for p in page["data"]]
        cursor = page["meta"]["next_cursor"]
    assert len(seen) == len(set(seen)) == 40
    assert client.get("/api/v1/people", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/people", params={"limit": 500}).status_code == 422


# --- people --------------------------------------------------------------------------------------


def test_people_directory(client: TestClient, world: synthetic_org.World) -> None:
    body = ok(client.get("/api/v1/people", params={"sort": "activity"}))
    assert body["meta"]["facets"]["status"] == [{"value": "active", "count": 40}]
    assert {f["value"] for f in body["meta"]["facets"]["unit"]} <= {
        "Department of Medicine",
        "Department of Pathology",
        "Department of Neurology",
        "Department of Epidemiology",
    }
    top = body["data"][0]
    assert top["counts"]["suggested"] >= body["data"][-1]["counts"]["suggested"]
    researcher = world.insiders[3]
    found = ok(client.get("/api/v1/people", params={"q": researcher.family}))["data"]
    assert found[0]["name"].endswith(researcher.family)
    assert MARK_START in found[0]["highlights"]["title"]


def test_person_profile(client: TestClient, world: synthetic_org.World) -> None:
    researcher = world.insiders[0]
    pid = person_id(client, researcher.family)
    data = ok(client.get(f"/api/v1/people/{pid}"))["data"]
    assert data["status"] == "active"
    [nih] = [i for i in data["identifiers"] if i["scheme"] == "nih_profile"]
    assert (nih["value"], nih["status"], nih["source"], nih["also"]) == (
        str(researcher.profile_id),
        "accepted",
        "reporter",
        [],
    )
    assert data["counts"]["grants"] == 1
    assert data["counts"]["publications"]["suggested"] >= len(researcher.papers)
    assert data["timeline"] and all(t["publications"] for t in data["timeline"])
    assert data["concepts"]
    assert client.get("/api/v1/people/per_missing").status_code == 404


def test_person_publications(client: TestClient, world: synthetic_org.World) -> None:
    researcher = world.insiders[1]
    pid = person_id(client, researcher.family)
    body = ok(client.get(f"/api/v1/people/{pid}/publications", params={"limit": 100}))
    pmids = {i["publication"]["pmid"] for i in body["data"]}
    assert set(researcher.papers) <= pmids
    item = body["data"][0]
    assert item["assertion"]["predicate"] == "authored" and item["assertion"]["evidence"]
    assert item["publication"]["title"] and item["publication"]["year"]
    years = [i["publication"]["year"] for i in body["data"]]
    assert years == sorted(years, reverse=True)
    assert "suggested:strong" in body["meta"]["tabs"]
    by_score = ok(client.get(f"/api/v1/people/{pid}/publications", params={"sort": "score"}))
    scores = [i["assertion"]["score"] for i in by_score["data"]]
    assert scores == sorted(scores, reverse=True)
    assert (
        client.get(f"/api/v1/people/{pid}/publications", params={"status": "maybe"}).status_code
        == 400
    )


def test_person_grants_studies_history(client: TestClient, world: synthetic_org.World) -> None:
    researcher = world.insiders[0]
    pid = person_id(client, researcher.family)
    grants = ok(client.get(f"/api/v1/people/{pid}/grants"))["data"]
    assert grants[0]["summary"]["core_project_num"] == researcher.grant
    studies = ok(client.get(f"/api/v1/people/{pid}/studies"))["data"]
    assert studies[0]["summary"]["nct_id"] == "NCT09000000"
    assert ok(client.get(f"/api/v1/people/{pid}/history"))["data"] == []


# --- records -------------------------------------------------------------------------------------


def test_publications(client: TestClient, world: synthetic_org.World) -> None:
    body = ok(client.get("/api/v1/publications", params={"year": "2024", "limit": 5}))
    assert body["meta"]["total"] > 0
    assert all(item["year"] == 2024 for item in body["data"])
    assert body["meta"]["facets"]["year"]
    researcher = world.insiders[2]
    pmid = researcher.papers[0]
    data = ok(client.get(f"/api/v1/publications/{pmid}"))["data"]
    assert data["pmid"] == pmid and data["url"].endswith(f"/{pmid}/")
    placed = [a for a in data["authors"] if a["people"]]
    assert any(p["person"]["name"].endswith(researcher.family) for a in placed for p in a["people"])
    assert data["provenance"]["run_id"] == "run_synthetic_pubmed"
    assert data["metrics"] is None
    assert client.get("/api/v1/publications/99999999").status_code == 404
    assert client.get("/api/v1/publications/abc").status_code == 404


def test_grants(client: TestClient, world: synthetic_org.World) -> None:
    body = ok(client.get("/api/v1/grants", params={"status": "active"}))
    assert body["meta"]["total"] == 30
    researcher = world.insiders[0]
    data = ok(client.get(f"/api/v1/grants/{researcher.grant.lower()}"))["data"]
    assert [y["fiscal_year"] for y in data["fiscal_years"]] == [2024, 2025, 2026]
    assert data["totals"]["total_awarded"] == 1_200_000
    assert (
        "not expenditures" in data["totals"]["counting_rule"].lower()
        or data["totals"]["counting_rule"]
    )
    assert data["investigators"][0]["person_id"]
    assert data["publications"] and data["studies"]
    assert client.get("/api/v1/grants/R01XX000000").status_code == 404


def test_studies(client: TestClient, world: synthetic_org.World) -> None:
    body = ok(client.get("/api/v1/studies"))
    assert body["meta"]["total"] == 4
    data = ok(client.get("/api/v1/studies/nct09000001"))["data"]
    assert data["registry_status"] == "RECRUITING"
    assert "not the organization's local recruitment status" in data["registry_status_note"]
    official = data["officials"][0]
    assert official["people"][0]["person"]["name"].endswith(world.insiders[1].family)
    assert data["organization_sites"] == 1 and data["sites"][0]["is_organization"]
    assert data["publications"][0]["summary"]["pmid"] == world.insiders[1].papers[0]
    assert client.get("/api/v1/studies/NCT00000000").status_code == 404


# --- curation ------------------------------------------------------------------------------------


def test_decisions(client: TestClient, world: synthetic_org.World) -> None:
    pid = person_id(client, world.insiders[5].family)
    item = ok(client.get(f"/api/v1/people/{pid}/publications"))["data"][0]
    aid = item["assertion"]["id"]
    data = ok(client.get(f"/api/v1/assertions/{aid}"))["data"]
    assert data["subject"]["label"] and data["object"]["label"] and data["history"] == []
    decided = ok(
        client.post(
            f"/api/v1/assertions/{aid}/decision", json={"action": "accept", "note": "checked"}
        )
    )["data"]
    assert decided["status"] == "accepted" and decided["locked"]
    assert decided["decided_by"] == "Operator" and decided["history"][0]["note"] == "checked"
    undone = ok(client.post(f"/api/v1/assertions/{aid}/decision", json={"action": "undo"}))["data"]
    assert undone["status"] == "suggested" and not undone["locked"]
    again = client.post(f"/api/v1/assertions/{aid}/decision", json={"action": "undo"})
    assert again.status_code == 409
    assert (
        client.post(f"/api/v1/assertions/{aid}/decision", json={"action": "maybe"}).status_code
        == 422
    )
    assert (
        client.post("/api/v1/assertions/asr_none/decision", json={"action": "accept"}).status_code
        == 404
    )


def test_review_queue_and_bulk(client: TestClient, world: synthetic_org.World) -> None:
    body = ok(client.get("/api/v1/review", params={"limit": 50}))
    assert body["meta"]["bands"]["strong"] > 0
    bands = [i["assertion"]["band"] for i in body["data"]]
    assert bands == sorted(bands, key=lambda b: {"strong": 0, "possible": 1}[b])
    assert body["meta"]["prefetch"] == 5
    first = body["data"][0]
    assert first["person"]["name"] and first["publication"]["title"]
    pid = person_id(client, world.insiders[6].family)
    mine = ok(client.get("/api/v1/review", params={"person": pid, "band": "strong"}))
    count = mine["meta"]["total"]
    bulk = ok(
        client.post(
            "/api/v1/review/bulk", json={"person_id": pid, "band": "strong", "action": "accept"}
        )
    )
    assert bulk["data"]["decided"] == count > 0
    after = ok(client.get("/api/v1/review", params={"person": pid, "band": "strong"}))
    assert after["meta"]["total"] == 0
    assert (
        client.post(
            "/api/v1/review/bulk", json={"person_id": "per_x", "band": "strong", "action": "accept"}
        ).status_code
        == 404
    )


def test_manual_addition(
    client: TestClient, world: synthetic_org.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    researcher = world.insiders[7]
    pid = person_id(client, researcher.family)
    elsewhere = next(a["pmid"] for a in world.pubmed if researcher.key not in str(a))
    created = client.post(f"/api/v1/people/{pid}/publications", json={"pmid": elsewhere})
    assert created.status_code == 201
    data = created.json()["data"]
    assert (data["status"], data["origin"], data["locked"]) == ("accepted", "human", True)

    api = pubmed_api()
    original = HttpClient.__init__

    def init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", api.transport())
        kwargs.setdefault("sleep", no_sleep)
        original(self, *args, **kwargs)

    monkeypatch.setattr(HttpClient, "__init__", init)
    fetched = client.post(f"/api/v1/people/{pid}/publications", json={"pmid": "31000004"})
    assert fetched.status_code == 201, fetched.text
    assert api.calls("efetch.fcgi")
    missing = client.post(f"/api/v1/people/{pid}/publications", json={"pmid": "39999999"})
    assert missing.status_code == 404
    assert client.post(f"/api/v1/people/{pid}/publications", json={"pmid": "x1"}).status_code == 422


# --- overview ------------------------------------------------------------------------------------


def test_search(client: TestClient, world: synthetic_org.World) -> None:
    grouped = ok(client.get("/api/v1/search", params={"q": "Immunotherapy"}))["data"]
    assert grouped["publication"]["total"] > 0 and grouped["person"]["items"]
    typed = ok(
        client.get(
            "/api/v1/search", params={"q": "Immunotherapy", "type": "publication", "limit": 3}
        )
    )
    assert len(typed["data"]) == 3 and typed["meta"]["next_cursor"]
    assert client.get("/api/v1/search", params={"q": "x", "type": "planet"}).status_code == 400
    empty = ok(client.get("/api/v1/search", params={"q": ""}))["data"]
    assert all(v["total"] == 0 for v in empty.values())


NASTY = [
    '"',
    '""',
    '"unclosed',
    "*",
    "^",
    "NEAR(",
    "a OR",
    "OR AND NOT",
    "title:x",
    "col:{a b}",
    "'; DROP TABLE doc; --",
    "\\",
    "\x00",
    "🧬🧪",
    "a" * 600,
    "(((",
    ")))",
    "-x",
    "+y",
    "a:b:c",
    "",
    "%",
    "_",
    "SELECT * FROM doc",
    "<script>alert(1)</script>",
    "  ",
    "-",
    ".",
]


@pytest.mark.parametrize("query", NASTY)
def test_malformed_search_never_errors(client: TestClient, query: str) -> None:
    for params in ({"q": query}, {"q": query, "type": "person"}):
        assert client.get("/api/v1/search", params=params).status_code in {200, 422}
    assert client.get("/api/v1/publications", params={"q": query}).status_code == 200


def test_stats_sources_runs(client: TestClient) -> None:
    stats = ok(client.get("/api/v1/stats"))["data"]
    assert stats["people"]["active"] >= 40 and stats["grants"]["active"] == 30
    assert stats["publications"] > 0 and stats["publications_by_year"]
    sources = ok(client.get("/api/v1/sources"))["data"]
    names = [s["name"] for s in sources["sources"]]
    assert names == ["pubmed", "icite", "reporter", "ctgov", "orcid", "mesh", "grants_gov"]
    assert sources["evidence"]["status"] == "succeeded"
    assert sources["search_index"]["documents"]["person"] == 40
    assert any(t["table"] == "pubmed_article" for t in sources["lake"])
    runs = ok(client.get("/api/v1/runs"))["data"]
    assert {r["source"] for r in runs} >= {"evidence", "search"}


def test_api_reference_page(client: TestClient) -> None:
    page = client.get("/api/docs")
    assert page.status_code == 200 and "API reference" in page.text
    spec = client.get("/api/openapi.json").json()
    for path in (
        "/api/v1/people",
        "/api/v1/people/{person_id}",
        "/api/v1/review",
        "/api/v1/assertions/{assertion_id}/decision",
        "/api/v1/search",
        "/api/v1/sources",
        "/api/v1/grants/{core_project_num}",
        "/api/v1/studies/{nct_id}",
    ):
        assert path in spec["paths"], path


def test_endpoints_explain_a_missing_index(instance: Instance) -> None:
    with TestClient(create_app(instance.config), base_url="http://localhost") as client:
        response = client.get("/api/v1/people")
        assert response.status_code == 503
        assert "tessera build search" in response.json()["detail"]
        assert ok(client.get("/api/v1/stats"))["data"]["publications"] is None


def test_highlight_markers_are_not_html(client: TestClient) -> None:
    items = ok(client.get("/api/v1/search", params={"q": "Study", "type": "publication"}))["data"]
    title = items[0]["highlights"]["title"]
    assert MARK_START in title and MARK_END in title and "<mark>" not in title


def test_an_index_from_before_kinds_were_indexed_answers_the_same(
    client: TestClient, tmp_path: Path
) -> None:
    """From 0.7.0 each document's kind is in the full-text index, so a search of one kind is
    counted and ranked from it alone. An index built before then answers the same, and a word
    that names a kind matches only the documents that contain it, never every one of that kind."""
    import shutil
    import sqlite3

    from tessera.search.index import index_path
    from tessera.search.query import SearchIndex

    home = client.app.state.home  # type: ignore[attr-defined]
    shutil.copy(index_path(home), tmp_path / "search.db")
    con = sqlite3.connect(tmp_path / "search.db")
    rows = con.execute("SELECT rowid, title, people, terms, body FROM doc_fts").fetchall()
    con.executescript(
        "DROP TABLE doc_fts; CREATE VIRTUAL TABLE doc_fts USING fts5(title, people, terms, body, "
        "tokenize = 'porter unicode61 remove_diacritics 2');"
    )
    con.executemany(
        "INSERT INTO doc_fts (rowid, title, people, terms, body) VALUES (?, ?, ?, ?, ?)", rows
    )
    con.commit()
    con.close()
    new, old = SearchIndex(index_path(home)), SearchIndex(tmp_path / "search.db")

    year = new.search("publication", "Immunotherapy").facets["year"][0]["value"]
    for kind, q, filters in [
        ("publication", "Immunotherapy", {}),
        ("publication", "Immunotherapy", {"year": [year]}),
        ("publication", "publication", {}),
        ("person", "Immunotherapy", {}),
        ("grant", "Immunotherapy", {}),
    ]:
        a = new.search(kind, q, filters, sort=None, limit=500)
        b = old.search(kind, q, filters, sort=None, limit=500)
        assert a.total == b.total and a.facets == b.facets, (kind, q, filters)
        assert {i["ref"] for i in a.items} == {i["ref"] for i in b.items}
        if kind in ("publication", "grant"):  # and in the same order, in a fixed sort
            by_year = [new.search(kind, q, filters, sort="year", limit=500), old.search(
                kind, q, filters, sort="year", limit=500)]  # fmt: skip
            assert [i["ref"] for i in by_year[0].items] == [i["ref"] for i in by_year[1].items]
    assert new.search("publication", "publication").total < new.counts()["publication"]
    grouped_new, grouped_old = new.grouped("Immunotherapy"), old.grouped("Immunotherapy")
    assert {k: r.total for k, r in grouped_new.items()} == {
        k: r.total for k, r in grouped_old.items()
    }
    titled = new.grouped("Study")["publication"].items  # a word in the titles: marked there
    assert titled and all(MARK_START in i["highlights"]["title"] for i in titled)


def test_a_searchs_facet_counts_are_kept_until_the_index_changes(
    client: TestClient, tmp_path: Path
) -> None:
    import shutil
    import sqlite3

    from tessera.search.index import index_path
    from tessera.search.query import SearchIndex

    home = client.app.state.home  # type: ignore[attr-defined]
    shutil.copy(index_path(home), tmp_path / "search.db")
    index = SearchIndex(tmp_path / "search.db")
    first = index.search("publication", "Immunotherapy", limit=1)
    years = {v["value"]: v["count"] for v in first.facets["year"]}
    again = index.search("publication", "Immunotherapy", limit=1, offset=1)
    assert again.facets == first.facets and again.items != first.items
    con = sqlite3.connect(tmp_path / "search.db")
    doc_id, year = con.execute(
        "SELECT d.id, f.value FROM doc d JOIN facet f ON f.doc_id = d.id "
        "WHERE d.ref = ? AND f.name = 'year'",
        [first.items[0]["ref"]],
    ).fetchone()
    con.execute("DELETE FROM facet WHERE doc_id = ? AND name = 'year'", [doc_id])
    con.commit()
    con.close()
    after = index.search("publication", "Immunotherapy", limit=1)
    counted = {v["value"]: v["count"] for v in after.facets["year"]}
    assert counted.get(year, 0) == years[year] - 1


def test_stored_facet_totals_stay_equal_to_live_counts(
    client: TestClient, world: synthetic_org.World
) -> None:
    """Unfiltered lists read facet totals stored with the index; triggers keep them exact as
    decisions rewrite documents between builds."""
    import sqlite3

    from tessera.search.index import index_path

    home = client.app.state.home  # type: ignore[attr-defined]

    def stored_and_live() -> tuple[dict, dict]:
        con = sqlite3.connect(index_path(home))
        try:
            stored = {(k, n, v): c for k, n, v, c in con.execute("SELECT * FROM facet_total")}
            live = {
                (k, n, v): c
                for k, n, v, c in con.execute(
                    "SELECT d.kind, f.name, f.value, count(*) FROM facet f "
                    "JOIN doc d ON d.id = f.doc_id GROUP BY 1, 2, 3"
                )
            }
        finally:
            con.close()
        return stored, live

    stored, live = stored_and_live()
    assert stored == live and stored
    pid = person_id(client, world.insiders[7].family)
    for item in ok(client.get(f"/api/v1/people/{pid}/publications"))["data"][:3]:
        aid = item["assertion"]["id"]
        ok(client.post(f"/api/v1/assertions/{aid}/decision", json={"action": "accept"}))
    stored, live = stored_and_live()
    assert stored == live
    listed = ok(client.get("/api/v1/publications", params={"limit": 5}))["meta"]["facets"]
    years = {v["value"]: v["count"] for v in listed["year"]}
    assert all(live[("publication", "year", y)] == n for y, n in years.items())


def test_the_queue_orders_by_flags_kept_from_the_evidence(
    client: TestClient, world: synthetic_org.World
) -> None:
    """Possible suggestions with a conflict come first, then ambiguous names, then the rest, as
    their evidence says; the flags the queue reads are kept in step with the evidence."""
    from sqlalchemy import select

    from tessera.evidence.store import REVIEW_CONFLICTS, review_flags
    from tessera.ledger.tables import assertion

    engine = make_engine(client.app.state.config.ledger_url)  # type: ignore[attr-defined]
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                select(assertion.c.evidence, assertion.c.review_flags).where(
                    assertion.c.predicate == "authored"
                )
            ).all()
    finally:
        engine.dispose()
    assert rows and all(review_flags(r[0]) == r[1] for r in rows)
    items = ok(client.get("/api/v1/review", params={"band": "possible", "limit": 200}))["data"]

    def rank(item: dict) -> int:
        features = {e["feature"] for e in item["assertion"]["evidence"]}
        return 1 if features & REVIEW_CONFLICTS else 2 if "name_ambiguity" in features else 3

    ranks = [rank(i) for i in items]
    assert ranks == sorted(ranks)


def test_the_queue_reads_one_page_in_the_order_it_always_had(
    client: TestClient, world: synthetic_org.World
) -> None:
    """The queue is counted in the database and only the page is read. Paged a few at a time, it
    is the same, item for item, as every open suggestion sorted in memory the way it was before:
    strong ones by person name, then possible ones with a conflict, an ambiguous name, and the
    rest, then unlikely ones; within each, the highest score first, then the ID."""
    from sqlalchemy import select

    from tessera.evidence.authorship import band
    from tessera.ledger.tables import assertion, person

    config = client.app.state.config  # type: ignore[attr-defined]
    engine = make_engine(config.ledger_url)
    try:
        with engine.connect() as conn:
            names = dict(
                conn.execute(
                    select(person.c.id, person.c.display_name).where(
                        person.c.status.in_(("active", "former"))
                    )
                ).all()
            )
            rows = conn.execute(
                select(
                    assertion.c.id,
                    assertion.c.subject_id,
                    assertion.c.score,
                    assertion.c.review_flags,
                ).where(
                    assertion.c.predicate == "authored",
                    assertion.c.status == "suggested",
                    assertion.c.reported.is_(True),
                )
            ).all()
    finally:
        engine.dispose()

    def key(r: Any) -> tuple:
        b, flags, score = band(r.score, config), r.review_flags or "", -(r.score or 0)
        if b == "strong":
            return (0, names.get(r.subject_id, ""), score, r.id)
        if b == "possible":
            return (1 if "conflict" in flags else 2 if "ambiguous" in flags else 3, score, r.id)
        return (4, score, r.id)

    shown = [r for r in rows if r.subject_id in names]
    assert {band(r.score, config) for r in shown} == {"strong", "possible", "unlikely"}
    assert len({r.subject_id for r in shown if band(r.score, config) == "strong"}) > 1
    pid = person_id(client, world.insiders[3].family)
    for bands, who in [
        (["strong", "possible"], None),
        (["strong", "possible", "unlikely"], None),
        (["possible"], None),
        (["unlikely"], None),
        (["strong", "possible", "unlikely"], pid),
    ]:
        expected = sorted(
            (r for r in rows if r.subject_id in names and band(r.score, config) in bands
             and (who is None or r.subject_id == who)), key=key)  # fmt: skip
        assert expected, bands
        params: dict[str, Any] = {"band": bands, "limit": 7}
        if who:
            params["person"] = who
        seen, offset = [], 0
        while True:
            page = ok(client.get("/api/v1/review", params={**params, "offset": offset}))
            seen += [i["assertion"]["id"] for i in page["data"]]
            if not page["data"]:
                break
            offset += 7
        assert seen == [r.id for r in expected], (bands, who)
        assert page["meta"]["total"] == len(expected)
        counted = page["meta"]["bands"]
        for b in bands:
            n = sum(1 for r in rows if r.subject_id in names and band(r.score, config) == b
                    and (who is None or r.subject_id == who))  # fmt: skip
            assert counted.get(b, 0) == n, (b, counted)
