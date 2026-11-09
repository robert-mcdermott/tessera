"""Profiles (PRF-01, PRF-02, PRF-06): the sections a researcher keeps, who may change them, and
the history of every change."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from tessera.api.app import create_app
from tessera.auth import passwords
from tessera.auth.accounts import Grant, create_account
from tessera.config import load_config
from tessera.demo import build_demo
from tessera.ledger.db import make_engine
from tessera.ledger.tables import assertion, person, profile_entry, unit
from tessera.profiles import store
from tessera.profiles.sections import ProfileError, clean

TODAY = date(2026, 11, 7)
PASSWORD = "correct horse battery staple"


@dataclass
class World:
    config: Any
    ids: dict[str, str]
    clients: dict[str, TestClient] = field(default_factory=dict)
    csrf: dict[str, str] = field(default_factory=dict)

    def call(self, who: str, method: str, path: str, body: Any = None) -> Any:
        response = self.clients[who].request(
            method, path, json=body, headers={"X-CSRF-Token": self.csrf[who]}
        )
        return response

    def ok(self, who: str, method: str, path: str, body: Any = None) -> Any:
        response = self.call(who, method, path, body)
        assert response.status_code in (200, 201), response.text
        return response.json()["data"]


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    path = tmp_path_factory.mktemp("profiles-demo")
    result = build_demo(path, today=TODAY)
    config_path = Path(result["config"])
    config_path.write_text(
        config_path.read_text("utf-8").replace("accounts = false", "accounts = true"), "utf-8"
    )
    config = load_config(config_path, env={})
    engine = make_engine(config.ledger_url)
    with engine.connect() as conn:
        uma = conn.execute(
            select(person.c.id).where(
                person.c.display_name == "Uma G Eriksen", person.c.status == "active"
            )
        ).scalar()
        other = conn.execute(
            select(person.c.id).where(
                person.c.display_name == "Valentina S Arnaud", person.c.status == "active"
            )
        ).scalar()
        lab = conn.execute(select(unit.c.id).where(unit.c.slug == "eriksen-lab")).scalar()
        candidate = conn.execute(select(person.c.id).where(person.c.status == "candidate")).scalar()
        confirmed = sorted(store.confirmed_papers(conn, uma))
        suggested = conn.execute(
            select(assertion.c.object_id).where(
                assertion.c.subject_id == uma,
                assertion.c.predicate == "authored",
                assertion.c.status == "suggested",
            )
        ).scalar()
    assert uma and other and lab and candidate and len(confirmed) >= 3
    cheap = passwords.SCRYPT_N
    passwords.SCRYPT_N = 2**10
    try:
        for name, grants, pid in (
            ("uma", [Grant("researcher")], uma),
            ("arnaud", [Grant("researcher")], other),
            ("guest", [Grant("viewer")], None),
            ("labcurator", [Grant("curator", "unit", lab)], None),
            ("helper", [Grant("viewer")], None),
        ):
            create_account(
                engine, name, name.title(), PASSWORD, grants, actor="test", person_id=pid
            )
    finally:
        passwords.SCRYPT_N = cheap
    engine.dispose()
    ids = {
        "uma": uma,
        "other": other,
        "candidate": candidate,
        "confirmed": ",".join(confirmed),
        "suggested": (suggested or "").removeprefix("pub:"),
    }
    w = World(config, ids)
    for name in ("uma", "arnaud", "guest", "labcurator", "helper"):
        client = TestClient(create_app(config), base_url="http://localhost")
        client.__enter__()
        signed = client.post("/api/v1/session", json={"username": name, "password": PASSWORD})
        assert signed.status_code == 200, signed.text
        w.clients[name], w.csrf[name] = client, signed.json()["data"]["csrf_token"]
    yield w
    for client in w.clients.values():
        client.__exit__(None, None, None)


def section(profile: dict[str, Any], sid: str) -> list[dict[str, Any]]:
    return next(s for s in profile["sections"] if s["id"] == sid)["entries"]


# --- the rules ---------------------------------------------------------------------------------


def test_each_section_checks_its_fields() -> None:
    assert clean(
        "position",
        {
            "title": " Assistant  Professor ",
            "organization": "Larkspur",
            "start": "2012-09",
            "end": "",
        },
    ) == {"title": "Assistant Professor", "organization": "Larkspur", "start": "2012-09"}
    assert clean("overview", {"text": "One.\r\n\r\n\r\nTwo  words."}) == {
        "text": "One.\n\nTwo words."
    }
    assert clean("featured", {"pmid": "PMID: 912346884"}) == {"pmid": "912346884"}
    problems = [
        ("position", {"organization": "Larkspur"}, "Title is required"),
        ("position", {"title": "x", "organization": "y", "salary": "1"}, "no 'salary'"),
        ("position", {"title": "x", "organization": "y", "start": "2012-13"}, "not a month"),
        (
            "position",
            {"title": "x", "organization": "y", "start": "2015", "end": "2012"},
            "cannot end",
        ),
        ("honor", {"title": "x", "year": "last year"}, "not a year"),
        ("honor", {"title": "x", "year": "2020-05-03"}, "a year, or a year and month"),
        (
            "talk",
            {"title": "x", "event": "y", "date": "2025-04-12", "kind": "lecture"},
            "is one of",
        ),
        ("link", {"label": "Lab", "url": "javascript:alert(1)"}, "web address"),
        ("overview", {"text": "x" * 5001}, "at most 5,000"),
        ("nonsense", {}, "no profile section"),
    ]
    for sid, data, message in problems:
        with pytest.raises(ProfileError, match=message):
            clean(sid, data)


# --- keeping a profile ---------------------------------------------------------------------------


def test_a_researcher_keeps_their_own_profile_and_every_change_is_recorded(world: World) -> None:
    uma = world.ids["uma"]
    base = f"/api/v1/people/{uma}/profile"
    mine = world.ok("uma", "GET", base)
    assert mine["may_edit"] is True and mine["capacity"] == "self"
    before = len(section(mine, "position"))
    added = world.ok(
        "uma",
        "POST",
        f"{base}/entries",
        {
            "section": "position",
            "fields": {
                "title": "Visiting Scientist",
                "organization": "Halden State University",
                "start": "2019",
                "end": "2020",
            },
        },
    )
    assert added["source"] == "manual" and added["created_by"] == "Uma"
    positions = section(world.ok("guest", "GET", base), "position")
    assert positions[0]["id"] == added["id"] and len(positions) == before + 1  # newest first
    world.ok(
        "uma",
        "POST",
        f"{base}/entries/{added['id']}",
        {
            "fields": {
                "title": "Visiting Professor",
                "organization": "Halden State University",
                "start": "2019",
                "end": "2020",
            }
        },
    )
    order = [e["id"] for e in section(world.ok("uma", "GET", base), "position")]
    world.ok("uma", "POST", f"{base}/order", {"section": "position", "ids": order[::-1]})
    assert [e["id"] for e in section(world.ok("uma", "GET", base), "position")] == order[::-1]
    world.ok("uma", "DELETE", f"{base}/entries/{added['id']}")
    history = world.ok("guest", "GET", f"{base}/history?limit=4")
    assert [h["action"] for h in history] == ["removed", "reordered", "changed", "added"]
    changed = history[2]
    assert changed["before"]["title"] == "Visiting Scientist"
    assert changed["after"]["title"] == "Visiting Professor"
    assert {h["capacity_label"] for h in history} == {"themselves"}
    assert history[1]["before"] == order and history[1]["after"] == order[::-1]


def test_others_read_but_only_its_keepers_change_it(world: World) -> None:
    uma = world.ids["uma"]
    base = f"/api/v1/people/{uma}/profile"
    entry = {"section": "honor", "fields": {"title": "Teaching Award", "year": "2021"}}
    for who in ("guest", "arnaud"):
        seen = world.ok(who, "GET", base)
        assert seen["may_edit"] is False and section(seen, "overview")  # every section is readable
        assert world.call(who, "POST", f"{base}/entries", entry).status_code == 403
    by_curator = world.ok("labcurator", "POST", f"{base}/entries", entry)
    history = world.ok("uma", "GET", f"{base}/history?limit=1")
    assert history[0]["capacity_label"] == "as curator" and history[0]["actor"] == "Labcurator"
    world.ok("uma", "DELETE", f"{base}/entries/{by_curator['id']}")
    # A curator scoped to the Eriksen Lab does not keep someone outside it.
    other = world.ids["other"]
    assert (
        world.call(
            "labcurator", "POST", f"/api/v1/people/{other}/profile/entries", entry
        ).status_code
        == 403
    )
    # Only people in the directory have profiles.
    candidate = world.ids["candidate"]
    assert world.call("labcurator", "GET", f"/api/v1/people/{candidate}/profile").status_code == 200
    assert (
        world.call("uma", "POST", f"/api/v1/people/{candidate}/profile/entries", entry).status_code
        == 403
    )


def test_featured_papers_are_confirmed_ones(world: World) -> None:
    uma = world.ids["uma"]
    base = f"/api/v1/people/{uma}/profile"
    featured = section(world.ok("uma", "GET", base), "featured")
    assert len(featured) == 2 and all(f["paper"]["title"] for f in featured)  # the demo's two
    confirmed = world.ids["confirmed"].split(",")
    spare = next(p for p in confirmed if p not in {f["fields"]["pmid"] for f in featured})
    if world.ids["suggested"]:
        refused = world.call(
            "uma",
            "POST",
            f"{base}/entries",
            {"section": "featured", "fields": {"pmid": world.ids["suggested"]}},
        )
        assert refused.status_code == 400 and "confirmed papers" in refused.text
    made = world.ok(
        "uma",
        "POST",
        f"{base}/entries",
        {"section": "featured", "fields": {"pmid": f"pub:{spare}"}},
    )
    again = world.call(
        "uma", "POST", f"{base}/entries", {"section": "featured", "fields": {"pmid": spare}}
    )
    assert again.status_code == 400 and "already featured" in again.text
    # A featured paper later rejected disappears for readers; its keepers still see it, marked.
    engine = make_engine(world.config.ledger_url)
    with engine.begin() as conn:
        conn.execute(
            update(assertion)
            .where(
                assertion.c.subject_id == uma,
                assertion.c.object_id == f"pub:{spare}",
                assertion.c.predicate == "authored",
            )
            .values(status="rejected")
        )
    try:
        assert spare not in {
            f["fields"]["pmid"] for f in section(world.ok("guest", "GET", base), "featured")
        }
        kept = {f["fields"]["pmid"]: f for f in section(world.ok("uma", "GET", base), "featured")}
        assert kept[spare]["confirmed"] is False
    finally:
        with engine.begin() as conn:
            conn.execute(
                update(assertion)
                .where(
                    assertion.c.subject_id == uma,
                    assertion.c.object_id == f"pub:{spare}",
                    assertion.c.predicate == "authored",
                )
                .values(status="accepted")
            )
        engine.dispose()
    world.ok("uma", "DELETE", f"{base}/entries/{made['id']}")


def test_one_overview_and_bad_orders_are_refused(world: World) -> None:
    base = f"/api/v1/people/{world.ids['uma']}/profile"
    second = world.call(
        "uma",
        "POST",
        f"{base}/entries",
        {"section": "overview", "fields": {"text": "Another overview."}},
    )
    assert second.status_code == 400 and "change it instead" in second.text
    links = [e["id"] for e in section(world.ok("uma", "GET", base), "link")]
    bad = world.call("uma", "POST", f"{base}/order", {"section": "link", "ids": links + links})
    assert bad.status_code == 400
    assert world.call("uma", "DELETE", f"{base}/entries/pfe_nothing").status_code == 404


def test_an_import_finds_its_own_entries_and_never_touches_hand_made_ones(world: World) -> None:
    engine = make_engine(world.config.ledger_url)
    uma = world.ids["uma"]
    try:
        first = store.upsert(
            engine,
            uma,
            "honor",
            {"title": "Imported Award", "year": "2018"},
            source="orcid",
            source_id="distinction/42",
            actor="ORCID import",
        )
        second = store.upsert(
            engine,
            uma,
            "honor",
            {"title": "Imported Award", "year": "2019"},
            source="orcid",
            source_id="distinction/42",
            actor="ORCID import",
        )
        assert first["id"] == second["id"] and second["fields"]["year"] == "2019"
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    select(profile_entry).where(
                        profile_entry.c.person_id == uma, profile_entry.c.section == "honor"
                    )
                )
                .mappings()
                .all()
            )
        assert sum(r["source"] == "orcid" for r in rows) == 1
        assert all(r["source"] == "manual" for r in rows if r["id"] != first["id"])
        with pytest.raises(ProfileError, match="names its source"):
            store.upsert(
                engine,
                uma,
                "honor",
                {"title": "x", "year": "2018"},
                source="manual",
                source_id="1",
                actor="x",
            )
        store.remove(engine, uma, first["id"], actor="test", capacity="admin")
    finally:
        engine.dispose()


def test_the_demo_fills_in_a_dozen_profiles(world: World) -> None:
    engine = make_engine(world.config.ledger_url)
    try:
        with engine.connect() as conn:
            people = {r.person_id for r in conn.execute(select(profile_entry.c.person_id))}
            sections = {r.section for r in conn.execute(select(profile_entry.c.section))}
    finally:
        engine.dispose()
    assert len(people) == 12
    assert sections == {
        "overview",
        "interests",
        "position",
        "education",
        "honor",
        "membership",
        "teaching",
        "talk",
        "link",
        "featured",
    }


# --- someone to act for them ---------------------------------------------------------------------


def test_a_researcher_names_someone_to_keep_their_profile(world: World) -> None:
    uma = world.ids["uma"]
    base = f"/api/v1/people/{uma}/profile"
    honor = {"section": "honor", "fields": {"title": "Kept by a helper", "year": "2022"}}
    assert world.call("helper", "POST", f"{base}/entries", honor).status_code == 403
    assert (
        world.call("guest", "POST", f"{base}/delegates", {"username": "helper"}).status_code == 403
    )
    for bad, message in (("nobody", "No active account"), ("uma", "own account")):
        refused = world.call("uma", "POST", f"{base}/delegates", {"username": bad})
        assert refused.status_code == 400 and message in refused.text
    named = world.ok("uma", "POST", f"{base}/delegates", {"username": "Helper"})
    assert named["name"] == "Helper" and named["username"] == "helper"
    assert world.call("uma", "POST", f"{base}/delegates", {"username": "helper"}).status_code == 400
    # Everyone sees who keeps it; only its keepers see the account behind the name.
    assert world.ok("guest", "GET", base)["delegates"] == [{"name": "Helper"}]
    assert world.ok("uma", "GET", base)["delegates"][0]["username"] == "helper"
    # The helper keeps the profile, on Uma's behalf, and finds it in their own list.
    mine = world.ok("helper", "GET", base)
    assert mine["may_edit"] is True and mine["capacity"] == "delegate" and mine["may_name"] is False
    entry = world.ok("helper", "POST", f"{base}/entries", honor)
    change = world.ok("guest", "GET", f"{base}/history?limit=1")[0]
    assert change["actor"] == "Helper" and change["capacity_label"] == "acting for them"
    assert world.ok("helper", "GET", "/api/v1/me/profiles") == [
        {"person_id": uma, "name": "Uma G Eriksen", "capacity": "delegate"}
    ]
    assert world.ok("uma", "GET", "/api/v1/me/profiles")[0]["capacity"] == "self"
    # Acting for them covers the profile only: not naming others, not their papers.
    assert (
        world.call("helper", "POST", f"{base}/delegates", {"username": "guest"}).status_code == 403
    )
    # (Turning the public page on and off is theirs too: see the public-choice test.)
    decided = world.call(
        "helper",
        "POST",
        f"/api/v1/people/{uma}/identifiers",
        {"action": "add", "scheme": "orcid", "value": "0000-0002-1825-0097"},
    )
    assert decided.status_code == 403
    # Removing them takes effect at once, and both changes are in the history.
    world.ok("helper", "DELETE", f"{base}/entries/{entry['id']}")
    world.ok("uma", "DELETE", f"{base}/delegates/{named['account_id']}")
    assert world.call("helper", "POST", f"{base}/entries", honor).status_code == 403
    assert world.ok("helper", "GET", "/api/v1/me/profiles") == []
    kept = [
        h
        for h in world.ok("guest", "GET", f"{base}/history?limit=10")
        if h["section"] == "delegates"
    ]
    assert [h["action"] for h in kept] == ["removed", "added"]
    assert kept[0]["section_label"] == "Who keeps the profile"


# --- public by choice ----------------------------------------------------------------------------


def _public_page(world: World, person_id: str) -> str | None:
    from tessera.home import DataHome
    from tessera.publish import pages as site_pages
    from tessera.publish.projection import project

    engine = make_engine(world.config.ledger_url)
    try:
        site = project(world.config, DataHome(world.config.data.home), engine, TODAY)
    finally:
        engine.dispose()
    files = site_pages.build(site, world.config)
    with make_engine(world.config.ledger_url).connect() as conn:
        name = conn.execute(select(person.c.display_name).where(person.c.id == person_id)).scalar()
    for path, html in files.items():
        if (
            path.startswith("people/")
            and path.endswith("/index.html")
            and f"<h1>{name}</h1>" in html
        ):
            return html
    return None


def test_the_researcher_chooses_what_the_public_page_shows(world: World) -> None:
    uma = world.ids["uma"]
    base = f"/api/v1/people/{uma}/profile"
    mine = world.ok("uma", "GET", base)
    assert [s["id"] for s in mine["public_sections"]][:3] == ["overview", "interests", "groups"]
    honors = section(mine, "honor")
    kept, dropped = honors[0], honors[-1]
    world.ok(
        "uma", "POST", f"{base}/public", {"public": True, "hidden_sections": ["talk", "grants"]}
    )
    world.ok("uma", "POST", f"{base}/entries/{dropped['id']}/public", {"hidden": True})
    assert (
        world.call("uma", "POST", f"{base}/public", {"hidden_sections": ["salary"]}).status_code
        == 400
    )
    page = _public_page(world, uma)
    assert page is not None
    assert "Overview" in page and kept["fields"]["title"] in page
    assert dropped["fields"]["title"] not in page
    assert "Talks and presentations" not in page and ">Grants<" not in page
    # Inside, everything stays, with the hidden entry marked for its keepers.
    inside = section(world.ok("guest", "GET", base), "honor")
    assert {e["id"] for e in inside} == {e["id"] for e in honors}
    # The preview is the same page, for the profile's keepers only.
    preview = world.clients["uma"].get(f"{base}/public/preview")
    assert preview.status_code == 200 and preview.headers["content-type"].startswith("text/html")
    assert dropped["fields"]["title"] not in preview.text and "Overview" in preview.text
    assert 'href="/api/v1/me/public-page.css"' in preview.text
    assert world.clients["uma"].get("/api/v1/me/public-page.css").status_code == 200
    assert world.clients["guest"].get(f"{base}/public/preview").status_code == 403
    # Someone acting for them may turn it off; then the page is gone from the next build.
    named = world.ok("uma", "POST", f"{base}/delegates", {"username": "helper"})
    world.ok("helper", "POST", f"/api/v1/people/{uma}/public", {"public": False})
    assert _public_page(world, uma) is None
    history = world.ok("guest", "GET", f"{base}/history?limit=6")
    public_changes = [h["action"] for h in history if h["section"] == "public"]
    assert public_changes[:2] == ["unpublished", "sections"]
    world.ok("uma", "POST", f"/api/v1/people/{uma}/public", {"public": True})
    world.ok("uma", "POST", f"{base}/public", {"hidden_sections": []})
    world.ok("uma", "POST", f"{base}/entries/{dropped['id']}/public", {"hidden": False})
    world.ok("uma", "DELETE", f"{base}/delegates/{named['account_id']}")
