"""Groups (wave 3, slice 2): kinds, nesting, sources and identifiers, memberships with roles,
merges, imports, the API, and curator scopes through groups."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from tessera.api.app import create_app
from tessera.auth.accounts import Grant
from tessera.auth.principal import Principal
from tessera.config import ConfigError, VocabularyConfig, example_config_text, parse_config
from tessera.core.clock import utc_now
from tessera.evidence import store as people
from tessera.evidence.names import make_name
from tessera.evidence.people import import_roster
from tessera.groups import store
from tessera.groups.importer import import_groups, parse_rows, read_file
from tessera.jobs.build import build_search
from tessera.ledger import migrate
from tessera.ledger.db import make_engine
from tessera.ledger.tables import (
    account,
    assertion,
    role_grant,
    unit,
    unit_event,
    unit_parent,
)

from .conftest import Instance

V = VocabularyConfig()
TODAY = date(2026, 11, 8)


@pytest.fixture
def engine(instance: Instance):  # type: ignore[no-untyped-def]
    engine = make_engine(instance.config.ledger_url)
    yield engine
    engine.dispose()


def person(engine, family: str, given: str, **ids: str) -> str:  # type: ignore[no-untyped-def]
    with engine.begin() as conn:
        pid = people.create_person(conn, make_name(family, given), status="active", source="test")
        for scheme, value in ids.items():
            people.add_identifier(conn, pid, scheme, value, source="test")
    return pid


def group(engine, name: str, kind: str = "lab", **extra: Any) -> str:  # type: ignore[no-untyped-def]
    parents = extra.pop("parents", ())
    with engine.begin() as conn:
        return store.create_group(
            conn, {"name": name, "kind": kind, **extra}, actor="Test", vocabulary=V,
            parents=parents,
        )  # fmt: skip


# --- the migration ----------------------------------------------------------------------------


def test_units_become_groups_and_back(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
    try:
        migrate.upgrade(engine, "0006")
        now = utc_now()
        with engine.begin() as conn:
            for uid, name, code, parent in (
                ("unt_a", "Division of Neuroscience", "NEURO", None),
                ("unt_b", "Neuroscience", None, "unt_a"),
                ("unt_c", "Neuroscience", None, None),
            ):
                conn.exec_driver_sql(
                    "INSERT INTO unit (id, name, kind, parent_id, code, created_at, updated_at) "
                    "VALUES (?, ?, 'department', ?, ?, ?, ?)",
                    (uid, name, parent, code, now.isoformat(), now.isoformat()),
                )
            conn.exec_driver_sql(
                "INSERT INTO person (id, display_name, status, created_at, updated_at) "
                "VALUES ('per_a', 'Ada Okafor', 'active', ?, ?)",
                (now.isoformat(), now.isoformat()),
            )
        with engine.begin() as conn:
            conn.execute(
                insert(assertion).values(
                    id="asr_a", subject_type="person", subject_id="per_a", predicate="member_of",
                    object_type="unit", object_id="unt_b", qualifier_key="",
                    qualifiers={"title": "Professor", "start": "2015"}, origin="source",
                    method="roster/v1", evidence=[], status="accepted", first_seen_at=now,
                    last_evaluated_at=now, updated_at=now,
                )
            )  # fmt: skip
        migrate.upgrade(engine)
        with engine.connect() as conn:
            rows = {r.id: r for r in conn.execute(select(unit))}
            assert rows["unt_a"].slug == "division-of-neuroscience"
            assert {rows["unt_b"].slug, rows["unt_c"].slug} == {"neuroscience", "neuroscience-2"}
            assert (rows["unt_a"].source, rows["unt_a"].external_id) == ("roster", "NEURO")
            assert rows["unt_b"].status == "active"
            assert conn.execute(select(unit_parent.c.child_id, unit_parent.c.parent_id)).all() == [
                ("unt_b", "unt_a")
            ]
            a = conn.execute(select(assertion)).mappings().one()
            assert a["qualifier_key"] == "member"
            assert a["qualifiers"] == {
                "title": "Professor", "start": "2015", "role": "member", "source": "roster"
            }  # fmt: skip
        migrate.downgrade(engine, "0006")
        with engine.connect() as conn:
            assert (
                conn.exec_driver_sql("SELECT parent_id FROM unit WHERE id = 'unt_b'").scalar()
                == "unt_a"
            )
            key, qualifiers = conn.exec_driver_sql(
                "SELECT qualifier_key, qualifiers FROM assertion"
            ).one()
            assert key == "" and json.loads(qualifiers) == {"title": "Professor", "start": "2015"}
    finally:
        engine.dispose()


# --- groups -----------------------------------------------------------------------------------


def test_create_edit_nest_and_archive(engine) -> None:  # type: ignore[no-untyped-def]
    clinical = group(engine, "Clinical Research Division", "division")
    basic = group(engine, "Basic Sciences Division", "division")
    program = group(engine, "Cancer Genomics", "program", parents=[clinical, basic])
    lab = group(engine, "Okafor Lab", parents=[program])
    with engine.begin() as conn:
        assert store.get_group(conn, program)["slug"] == "cancer-genomics"
        # A second group of the same name gets its own address.
        twin = store.create_group(conn, {"name": "Cancer Genomics", "kind": "team"},
                                  actor="Test", vocabulary=V)  # fmt: skip
        assert store.get_group(conn, twin)["slug"] == "cancer-genomics-2"
        # A program spans two divisions, and a division's scope reaches the lab through it.
        assert {p["id"] for p in store.parents_of(conn, [program])[program]} == {clinical, basic}
        assert store.descendants(conn, [basic]) == {basic, program, lab}
        with pytest.raises(store.GroupError, match="inside this group"):
            store.set_parents(conn, clinical, [lab], actor="Test")
        with pytest.raises(store.GroupError, match="not a kind of group"):
            store.create_group(conn, {"name": "X", "kind": "guild"}, actor="Test", vocabulary=V)
        changed = store.update_group(
            conn, program, {"name": "Cancer Genomics Program", "code": "CGP"}, actor="Ann",
            vocabulary=V,
        )  # fmt: skip
        assert changed["name"] == ["Cancer Genomics", "Cancer Genomics Program"]
        # The address stays when the name changes, so links keep working.
        assert store.resolve(conn, "cancer-genomics")["id"] == program
        assert store.resolve(conn, "CGP")["id"] == program
        store.update_group(conn, lab, {"status": "archived"}, actor="Ann", vocabulary=V)
        actions = [e["action"] for e in store.events(conn, lab)]
        assert actions == ["archive", "nest", "create"]
        with pytest.raises(store.GroupError, match="already belongs"):
            store.update_group(conn, lab, {"code": "CGP"}, actor="Ann", vocabulary=V)


def test_memberships_roles_dates_and_alumni(engine) -> None:  # type: ignore[no-untyped-def]
    lab = group(engine, "Okafor Lab")
    ada, bo = person(engine, "Okafor", "Ada"), person(engine, "Lindqvist", "Bo")
    with engine.begin() as conn:
        lead, action = store.add_membership(
            conn, person_id=ada, group_id=lab, vocabulary=V, actor="Ann", role="lead",
            start="2015",
        )  # fmt: skip
        assert action == "created"
        # The same person may hold a second role in the same group.
        store.add_membership(conn, person_id=ada, group_id=lab, vocabulary=V, actor="Ann")
        bo_id, _ = store.add_membership(
            conn, person_id=bo, group_id=lab, vocabulary=V, actor="Ann", role="trainee",
            title="Postdoctoral fellow", start="2019-07",
        )  # fmt: skip
        with pytest.raises(store.GroupError, match="not a membership role"):
            store.add_membership(conn, person_id=bo, group_id=lab, vocabulary=V, actor="Ann",
                                 role="emperor")  # fmt: skip
        with pytest.raises(store.GroupError, match="before it starts"):
            store.update_membership(conn, bo_id, {"end": "2018"}, actor="Ann", vocabulary=V)
        store.update_membership(conn, bo_id, {"end": "2024-06"}, actor="Ann", vocabulary=V)
        counts = store.member_counts(conn, TODAY)[lab]
        assert counts == {"current": 1, "alumni": 1}  # Ada once, though she holds two roles
        view = store.membership_view(store.get_membership(conn, lead), V, TODAY)
        assert (view["role_label"], view["leader"], view["current"]) == ("Lead", True, True)
        actions = [e["action"] for e in store.events(conn, lab)]
        assert actions[0] == "member_end"
        # Removing is for mistakes: the membership is rejected, and a source cannot bring it back.
        store.remove_membership(conn, bo_id, actor="Ann", note="Never in this lab")
        assert store.memberships(conn, group_ids=[lab], person_ids=[bo]) == []
        _, again = store.add_membership(
            conn, person_id=bo, group_id=lab, vocabulary=V, actor="hr import", role="trainee",
            source="hr",
        )  # fmt: skip
        assert again == "kept"


def test_merge_moves_memberships_subgroups_and_roles(engine) -> None:  # type: ignore[no-untyped-def]
    division = group(engine, "Division of Neuroscience", "division")
    old = group(engine, "Neurobiology", "department", parents=[division])
    new = group(engine, "Neuroscience", "department")
    sub = group(engine, "Sleep Lab", parents=[old])
    ada, bo = person(engine, "Okafor", "Ada"), person(engine, "Lindqvist", "Bo")
    with engine.begin() as conn:
        store.add_membership(conn, person_id=ada, group_id=old, vocabulary=V, actor="T",
                             start="2010", end="2016")  # fmt: skip
        store.add_membership(conn, person_id=ada, group_id=new, vocabulary=V, actor="T",
                             start="2014")  # fmt: skip
        store.add_membership(conn, person_id=bo, group_id=old, vocabulary=V, actor="T")
        now = utc_now()
        conn.execute(
            insert(account).values(id="usr_x", username="cy", display_name="Cy", password_hash="x",
                                   created_at=now, updated_at=now, password_changed_at=now)
        )  # fmt: skip
        conn.execute(
            insert(role_grant).values(account_id="usr_x", role="curator", scope_type="unit",
                                      scope_id=old, granted_by="t", granted_at=now)
        )  # fmt: skip
        conn.execute(update_external(old, "hr", "N-1"))
        moved = store.merge_groups(conn, old, new, actor="Ann")
        assert moved["memberships"] == 1 and moved["combined"] == 1
        assert moved["subgroups"] == 1 and moved["parents"] == 1 and moved["roles"] == 1
        assert conn.execute(select(role_grant.c.scope_id)).scalar() == new
        [ada_membership] = store.memberships(conn, group_ids=[new], person_ids=[ada])
        # The same role in both: one membership whose dates cover both.
        assert ada_membership["qualifiers"]["start"] == "2010"
        assert "end" not in ada_membership["qualifiers"]
        assert store.descendants(conn, [division]) == {division, new, sub}
        assert store.resolve(conn, "neurobiology")["id"] == new  # the old address still works
        assert store.get_group(conn, old)["status"] == "merged"
        # An import naming the merged group's identifier updates the group it became.
        found, action = store.upsert_group(
            conn, {"name": "Neuroscience", "short_name": "NS"}, source="hr", external_id="N-1",
            actor="hr import", vocabulary=V,
        )  # fmt: skip
        assert (found, action) == (new, "updated")
        with pytest.raises(store.GroupError, match="already merged"):
            store.merge_groups(conn, old, new, actor="Ann")


def update_external(group_id: str, source: str, external_id: str):  # type: ignore[no-untyped-def]
    from sqlalchemy import update

    return update(unit).where(unit.c.id == group_id).values(source=source, external_id=external_id)


# --- imports ----------------------------------------------------------------------------------


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_import_groups_then_memberships_and_again(engine, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    ada = person(engine, "Okafor", "Ada", orcid="0000-0002-1825-0097")
    bo = person(engine, "Lindqvist", "Bo", email="bo@example.edu")
    groups_csv = write(
        tmp_path / "groups.csv",
        "id,name,kind,parents,code\n"
        "D1,Clinical Research Division,division,,CRD\n"
        "D2,Basic Sciences Division,division,,\n"
        "P1,Cancer Genomics,program,D1;D2,\n"
        "L1,Okafor Lab,lab,P1,\n"
        "X1,Guild of Pipettes,guild,,\n"
        "L2,Orphan Lab,lab,NOPE,\n",
    )
    data = read_file(groups_csv)
    stats = import_groups(engine, data, source="hr", vocabulary=V, today=TODAY)
    assert stats.groups["created"] == 5
    assert stats.skipped == [
        "group row 4: 'guild' is not a kind of group here (division, department, program, "
        "center, institute, core, lab, team); add it to [vocabulary.kinds] first.",
        "group row 5: parent 'NOPE' is not a known group",
    ]
    members_csv = write(
        tmp_path / "members.csv",
        "group,orcid,email,role,title,start,id\n"
        "L1,0000-0002-1825-0097,,lead,Professor,2015,M1\n"
        "L1,,bo@example.edu,trainee,Postdoctoral fellow,2021,M2\n"
        "L1,,nobody@example.edu,member,,,M3\n",
    )
    stats = import_groups(engine, read_file(members_csv), source="hr", vocabulary=V, today=TODAY)
    assert stats.memberships == {"created": 2}
    assert "nobody@example.edu is not in the directory" in stats.skipped[0]
    assert stats.people == {ada, bo}

    # Again, with a rename, a role change, and Bo gone: an update, not duplicates.
    write(
        groups_csv,
        "id,name,kind,parents\n"
        "D1,Clinical Research Division,division,\n"
        "D2,Basic Sciences Division,division,\n"
        "P1,Cancer Genomics Program,program,D1\n"
        "L1,Okafor Lab,lab,P1\n",
    )
    stats = import_groups(engine, read_file(groups_csv), source="hr", vocabulary=V,
                          complete=True, today=TODAY)  # fmt: skip
    assert stats.groups["updated"] == 1 and stats.groups["nested"] == 1
    assert stats.groups["archived"] == 1  # the orphan lab is no longer listed
    write(members_csv, "group,orcid,role,start,id\nL1,0000-0002-1825-0097,co_lead,2015,M1\n")
    stats = import_groups(engine, read_file(members_csv), source="hr", vocabulary=V,
                          complete=True, today=TODAY)  # fmt: skip
    assert stats.memberships["ended"] == 1
    with engine.connect() as conn:
        assert conn.execute(select(unit.c.id).where(unit.c.source == "hr")).all().__len__() == 5
        program = store.find_for_import(conn, source="hr", external_id="P1", code=None, name=None)
        assert program["name"] == "Cancer Genomics Program"
        assert [p["name"] for p in store.parents_of(conn, [program["id"]])[program["id"]]] == [
            "Clinical Research Division"
        ]
        lab = store.find_for_import(conn, source="hr", external_id="L1", code=None, name=None)
        views = {
            (v["person_id"], v["role"]): v
            for v in (
                store.membership_view(r, V, TODAY)
                for r in store.memberships(conn, group_ids=[lab["id"]])
            )
        }
        # Ada's membership changed role in place; Bo's ended today and he is an alumnus.
        assert set(views) == {(ada, "co_lead"), (bo, "trainee")}
        assert views[(bo, "trainee")]["end"] == TODAY.isoformat()


def test_dry_run_changes_nothing_and_json_holds_both(engine) -> None:  # type: ignore[no-untyped-def]
    ada = person(engine, "Okafor", "Ada", local="E100")
    data = parse_rows(
        {
            "groups": [{"id": "L1", "name": "Okafor Lab", "kind": "lab"}],
            "memberships": [{"group": "L1", "local_id": "E100", "role": "lead"}],
        }
    )
    stats = import_groups(engine, data, source="registry", vocabulary=V, dry_run=True)
    assert (stats.groups["created"], stats.memberships["created"]) == (1, 1)
    with engine.connect() as conn:
        assert conn.execute(select(unit.c.id)).all() == []
    stats = import_groups(engine, data, source="registry", vocabulary=V)
    with engine.connect() as conn:
        [row] = store.memberships(conn, person_ids=[ada])
        assert row["qualifiers"] == {"role": "lead", "source": "registry"}
    with pytest.raises(store.GroupError, match="name the import"):
        import_groups(engine, data, source="manual", vocabulary=V)


def test_a_feed_adopts_a_group_made_by_hand(engine) -> None:  # type: ignore[no-untyped-def]
    made = group(engine, "Shared Microscopy", "core", code="MIC")
    row = {"id": "C9", "name": "Microscopy Core", "kind": "core", "code": "MIC"}
    stats = import_groups(engine, parse_rows([row]), source="hr", vocabulary=V)
    assert stats.groups == {"updated": 1}
    with engine.connect() as conn:
        row = store.get_group(conn, made)
        assert (row["source"], row["external_id"], row["name"]) == ("hr", "C9", "Microscopy Core")


def test_the_roster_is_one_way_in(engine, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    roster = write(
        tmp_path / "roster.csv",
        "family,given,email,group,group_code,kind,role,title,start\n"
        "Okafor,Ada,ada@example.edu,Okafor Lab,OKL,lab,lead,Professor,2015\n"
        "Okafor,Ada,ada@example.edu,Division of Neuroscience,DN,division,,Professor,2015\n",
    )
    import_roster(engine, roster, vocabulary=V)
    import_roster(engine, roster, vocabulary=V)  # again: nothing doubles
    with engine.connect() as conn:
        rows = {r.name: r for r in conn.execute(select(unit))}
        assert set(rows) == {"Okafor Lab", "Division of Neuroscience"}
        assert (rows["Okafor Lab"].kind, rows["Okafor Lab"].source) == ("lab", "roster")
        assert rows["Okafor Lab"].external_id == "OKL"
        roles = sorted(r["qualifier_key"] for r in store.memberships(conn))
        assert roles == ["lead", "member"]


# --- configuration ----------------------------------------------------------------------------


def test_kinds_and_roles_are_configurable(tmp_path: Path) -> None:
    import tomllib

    raw = tomllib.loads(example_config_text())
    raw["vocabulary"] = {
        "group": "Unit",
        "kinds": {"school": "School", "lab": "Laboratory"},
        "roles": {"pi": "Principal investigator", "member": "Member"},
        "leader_roles": ["pi"],
    }
    config = parse_config(raw, base_dir=tmp_path)
    assert config.vocabulary.kind_label("lab") == "Laboratory"
    assert config.vocabulary.default_kind == "school"
    assert config.vocabulary.to_json()["roles"][0] == {
        "id": "pi", "label": "Principal investigator", "leader": True
    }  # fmt: skip
    raw["vocabulary"]["leader_roles"] = ["chief"]
    raw["vocabulary"]["kinds"] = {"Bad Key": "x"}
    with pytest.raises(ConfigError) as caught:
        parse_config(raw, base_dir=tmp_path)
    text = "\n".join(caught.value.problems)
    assert "'chief' is not in [vocabulary.roles]" in text and "lowercase letters" in text


# --- curator scope through groups -------------------------------------------------------------


def test_a_scope_reaches_people_in_groups_beneath_through_any_parent(engine) -> None:  # type: ignore[no-untyped-def]
    a = group(engine, "Division A", "division")
    b = group(engine, "Division B", "division")
    program = group(engine, "Shared Program", "program", parents=[a, b])
    ada, bo = person(engine, "Okafor", "Ada"), person(engine, "Lindqvist", "Bo")
    with engine.begin() as conn:
        store.add_membership(conn, person_id=ada, group_id=program, vocabulary=V, actor="T")
        store.add_membership(conn, person_id=bo, group_id=a, vocabulary=V, actor="T",
                             start="2010", end="2012")  # fmt: skip
    curator = Principal(name="Cy", grants=(Grant("curator", "unit", b),))
    assert curator.curated_people(engine) == {ada}  # Bo is in A, and his membership ended


# --- the API ----------------------------------------------------------------------------------


def test_the_api_curates_groups_and_memberships(instance: Instance, engine) -> None:  # type: ignore[no-untyped-def]
    ada = person(engine, "Okafor", "Ada")
    build_search(instance.config, instance.home, engine)
    with TestClient(create_app(instance.config), base_url="http://localhost") as client:
        division = client.post(
            "/api/v1/groups", json={"name": "Division of Neuroscience", "kind": "division"}
        )
        assert division.status_code == 201, division.text
        division_id = division.json()["data"]["id"]
        lab = client.post(
            "/api/v1/groups",
            json={"name": "Okafor Lab", "kind": "lab", "parents": ["division-of-neuroscience"]},
        ).json()["data"]
        assert lab["slug"] == "okafor-lab" and lab["parents"][0]["id"] == division_id
        assert client.post("/api/v1/groups", json={"name": "X", "kind": "guild"}).status_code == 400

        added = client.post(
            "/api/v1/groups/okafor-lab/members",
            json={"person_id": ada, "role": "lead", "start": "2015"},
        )
        assert added.status_code == 201, added.text
        membership = added.json()["data"]
        assert (membership["role_label"], membership["current"]) == ("Lead", True)
        # The directory knows at once, without a rebuild.
        listed = client.get("/api/v1/people", params={"unit": "Okafor Lab"}).json()["data"]
        assert [p["id"] for p in listed] == [ada]
        profile = client.get(f"/api/v1/people/{ada}").json()["data"]
        assert profile["units"][0]["role"] == "lead" and profile["units"][0]["kind"] == "lab"

        renamed = client.post("/api/v1/groups/okafor-lab", json={"name": "Okafor Laboratory"})
        assert renamed.json()["data"]["name"] == "Okafor Laboratory"
        assert client.get("/api/v1/people", params={"unit": "Okafor Laboratory"}).json()["data"]

        ended = client.post(
            f"/api/v1/groups/okafor-lab/members/{membership['id']}", json={"end": "2025"}
        ).json()["data"]
        assert ended["current"] is False
        alumni = client.get("/api/v1/groups/okafor-lab/members", params={"include": "alumni"})
        assert [m["name"] for m in alumni.json()["data"]] == ["Ada Okafor"]
        assert client.get("/api/v1/groups/okafor-lab/members").json()["data"] == []

        listing = client.get("/api/v1/groups", params={"kind": "lab"}).json()
        assert [g["name"] for g in listing["data"]] == ["Okafor Laboratory"]
        assert listing["data"][0]["alumni"] == 1
        detail = client.get(f"/api/v1/groups/{division_id}").json()["data"]
        assert [c["name"] for c in detail["children"]] == ["Okafor Laboratory"]

        archived = client.post("/api/v1/groups/okafor-lab", json={"status": "archived"})
        assert archived.json()["data"]["status"] == "archived"
        assert client.get("/api/v1/groups", params={"kind": "lab"}).json()["data"] == []
        history = client.get("/api/v1/groups/okafor-lab/history").json()["data"]
        assert [e["action"] for e in history][:3] == ["archive", "member_end", "edit"]
        assert history[1]["person"] == "Ada Okafor"

        imported = client.post(
            "/api/v1/groups/import",
            params={"source": "hr", "dry_run": True},
            json={"groups": [{"id": "C1", "name": "Imaging Core", "kind": "core"}]},
        ).json()["data"]
        assert imported["groups"] == {"created": 1} and imported["dry_run"] is True
        assert client.get("/api/v1/groups", params={"q": "imaging"}).json()["data"] == []

        other = client.post("/api/v1/groups", json={"name": "Neurobiology", "kind": "lab"})
        merged = client.post(
            f"/api/v1/groups/{other.json()['data']['id']}/merge", json={"into": "okafor-lab"}
        )
        assert merged.status_code == 200, merged.text
        assert client.get("/api/v1/groups/neurobiology").json()["data"]["id"] == lab["id"]
        meta = client.get("/api/v1/meta").json()["data"]
        assert meta["vocabulary"]["kinds"][0] == {"id": "division", "label": "Division"}
    with engine.connect() as conn:
        assert conn.execute(select(unit_event.c.actor).limit(1)).scalar() == "Operator"


# --- group pages (LAB-04, LAB-05) ------------------------------------------------------------


def test_page_blocks_are_checked(engine) -> None:  # type: ignore[no-untyped-def]
    from tessera.groups import pages

    clean = pages.validate_blocks(
        [
            {"type": "about", "text": "  <script>alert(1)</script>\r\nHello  "},
            {"type": "publications", "featured": ["123", 456], "limit": 5},
            {"type": "news", "items": [{"title": "Hi", "date": "2026-11-09"}]},
            {"type": "about", "title": "Second"},
        ]
    )
    assert clean[0]["text"] == "<script>alert(1)</script>\nHello"  # stays text, never markup
    assert clean[1]["featured"] == ["123", "456"] and len({b["id"] for b in clean}) == 4
    for bad, message in (
        ([{"type": "iframe"}], "not one of the blocks"),
        ([{"type": "people"}, {"type": "people"}], "one People block"),
        ([{"type": "people", "html": "<b>"}], "no 'html'"),
        ([{"type": "news", "items": [{"title": "x", "url": "javascript:alert(1)"}]}], "https://"),
        ([{"type": "news", "items": [{"title": "x", "date": "yesterday"}]}], "2026-11-09"),
        ([{"type": "publications", "featured": ["not a pmid"]}], "PubMed IDs"),
        ([{"type": "grants", "limit": 500}], "1 to 50"),
    ):
        with pytest.raises(store.GroupError, match=message):
            pages.validate_blocks(bad)


def test_draft_publish_history_and_rollback(engine) -> None:  # type: ignore[no-untyped-def]
    from tessera.groups import pages

    lab = group(engine, "Okafor Lab")
    with engine.begin() as conn:
        blocks, default = pages.published_blocks(conn, lab)
        assert default and [b["type"] for b in blocks][:2] == ["about", "people"]
        with pytest.raises(store.GroupError, match="no draft"):
            pages.publish(conn, lab, actor="Ann")
        pages.save_draft(conn, lab, [{"type": "about", "text": "One"}], actor="Ann")
        assert pages.publish(conn, lab, actor="Ann", note="First") == 1
        assert pages.get_page(conn, lab)["draft"] is None  # publishing clears the draft
        pages.save_draft(conn, lab, [{"type": "about", "text": "Two"}], actor="Bo")
        assert pages.publish(conn, lab, actor="Bo") == 2
        assert pages.rollback(conn, lab, 1, actor="Ann", note="Back") == 3
        blocks, default = pages.published_blocks(conn, lab)
        assert not default and blocks[0]["text"] == "One"
        assert [(r["number"], r["action"]) for r in pages.revisions(conn, lab)] == [
            (3, "rollback"), (2, "publish"), (1, "publish"),
        ]  # fmt: skip
        with pytest.raises(store.GroupError, match="no version 9"):
            pages.rollback(conn, lab, 9, actor="Ann")
        actions = [e["action"] for e in store.events(conn, lab)][:3]
        assert actions == ["page_rollback", "page_publish", "page_draft"]


def test_a_manager_scope_covers_the_groups_below_only_when_granted(engine) -> None:  # type: ignore[no-untyped-def]
    division = group(engine, "Division A", "division")
    lab = group(engine, "Lab A", parents=[division])
    only = Principal(name="M", grants=(Grant("manager", "unit", division),))
    below = Principal(name="N", grants=(Grant("manager", "unit", division, True),))
    curator = Principal(name="C", grants=(Grant("curator", "unit", division),))
    assert only.edits_group(engine, division) and not only.edits_group(engine, lab)
    assert below.edits_group(engine, lab) and curator.edits_group(engine, lab)
    assert not only.reviews and only.manages_any
