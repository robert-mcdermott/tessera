"""Accounts, roles, and scopes (PLT-06, CUR-06): every route, every role."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from tessera.api.access import POLICIES, Level
from tessera.api.app import create_app
from tessera.auth import passwords
from tessera.auth.accounts import Grant, create_account, find_account
from tessera.config import Config, example_config_text, load_config
from tessera.core.clock import utc_now
from tessera.evidence import store
from tessera.evidence.people import import_roster
from tessera.evidence.resolve import resolve
from tessera.home import DataHome
from tessera.jobs.build import build_search
from tessera.ledger import migrate
from tessera.ledger.db import make_engine
from tessera.ledger.tables import auth_session, unit

from . import synthetic_org

PASSWORD = "a long enough passphrase"
LOCAL = "http://localhost"


@dataclass
class World:
    config: Config
    app_config_path: Path
    ids: dict[str, str] = field(default_factory=dict)  # named sample IDs
    clients: dict[str, TestClient] = field(default_factory=dict)
    csrf: dict[str, str] = field(default_factory=dict)


def _accounts_config(tmp: Path) -> Path:
    text = example_config_text().replace("accounts = false", "accounts = true")
    path = tmp / "tessera.toml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    tmp = tmp_path_factory.mktemp("access")
    path = _accounts_config(tmp)
    config = load_config(path, env={})
    home = DataHome(config.data.home)
    home.create()
    engine = make_engine(config.ledger_url)
    migrate.upgrade(engine)
    synthetic = synthetic_org.build(tmp)
    synthetic_org.load(synthetic, home.lake)
    import_roster(engine, synthetic.roster_path)
    resolve(config, home, engine, today=date(2026, 11, 7))
    build_search(config, home, engine)

    # Two roster members in different units, both with suggestions.
    people = {p.id: p for p in store.load_people(engine)}
    suggested = store.assertions_for(engine, predicate="authored", statuses=("suggested",))
    with engine.connect() as conn:
        units = dict(conn.execute(select(unit.c.id, unit.c.name)).all())
    membership = {
        r["subject_id"]: r["object_id"] for r in store.assertions_for(engine, predicate="member_of")
    }
    by_person: dict[str, list[dict[str, Any]]] = {}
    for r in suggested:
        by_person.setdefault(r["subject_id"], []).append(r)
    inside = next(pid for pid in membership if pid in by_person)
    outside = next(
        pid for pid in membership if pid in by_person and membership[pid] != membership[inside]
    )
    researcher = next(
        pid for pid in by_person if pid not in membership and len(by_person[pid]) >= 2
    )
    ids = {
        "unit": membership[inside],
        "unit_name": units[membership[inside]],
        "inside": inside,
        "outside": outside,
        "researcher": researcher,
        "inside_asr": by_person[inside][0]["id"],
        "outside_asr": by_person[outside][0]["id"],
        "own_asr": by_person[researcher][0]["id"],
        "pmid": by_person[inside][0]["object_id"].removeprefix("pub:"),
        "inside_name": people[inside].display_name,
    }
    cheap = passwords.SCRYPT_N
    passwords.SCRYPT_N = 2**10  # fast hashing for tests; verification reads N from the hash
    try:
        roles = {
            "admin": ([Grant("admin")], None),
            "curator": ([Grant("curator")], None),
            "unitcur": ([Grant("curator", "unit", ids["unit"])], None),
            "researcher": ([Grant("researcher")], researcher),
            "viewer": ([Grant("viewer")], None),
        }
        for name, (grants, person_id) in roles.items():
            create_account(
                engine,
                name,
                name.title(),
                PASSWORD,
                grants,
                actor="test",
                person_id=person_id,
            )
    finally:
        passwords.SCRYPT_N = cheap
    engine.dispose()
    w = World(config, path, ids)
    for name in ("admin", "curator", "unitcur", "researcher", "viewer"):
        client = TestClient(create_app(config), base_url=LOCAL)
        client.__enter__()
        signed = client.post("/api/v1/session", json={"username": name, "password": PASSWORD})
        assert signed.status_code == 200, signed.text
        w.clients[name] = client
        w.csrf[name] = signed.json()["data"]["csrf_token"]
    anonymous = TestClient(create_app(config), base_url=LOCAL)
    anonymous.__enter__()
    w.clients["anonymous"] = anonymous
    yield w
    for client in w.clients.values():
        client.__exit__(None, None, None)


def call(w: World, who: str, method: str, path: str, body: Any = None, **headers: str):  # type: ignore[no-untyped-def]
    client = w.clients[who]
    if method != "GET" and who in w.csrf:
        headers.setdefault("X-CSRF-Token", w.csrf[who])
    return client.request(method, path, json=body, headers=headers)


# --- the policy table covers every route ---------------------------------------------------------


def test_every_api_route_has_a_policy(world: World) -> None:
    app = create_app(world.config)
    routes = {
        (method.upper(), path) for path, ops in app.openapi()["paths"].items() for method in ops
    }
    routes |= {("GET", "/api/docs"), ("GET", "/api/openapi.json")}
    assert routes - set(POLICIES) == set(), "routes without an access policy"
    assert set(POLICIES) - routes == set(), "policies for routes that do not exist"


# --- the role matrix ---------------------------------------------------------------------------

ROLE_LEVELS = {
    "viewer": Level.READ,
    "researcher": Level.REVIEW,
    "unitcur": Level.CURATE_ANY,
    "curator": Level.CURATE_ALL,
    "admin": Level.ADMIN,
}


def concrete(w: World, path: str) -> str:
    return (
        path.replace("{person_id}", w.ids["inside"])
        .replace("{assertion_id}", w.ids["inside_asr"])
        .replace("{pmid}", w.ids["pmid"])
        .replace("{core}", "R01XX000000")
        .replace("{nct_id}", "NCT00000000")
        .replace("{merge_id}", "mrg_missing")
        .replace("{account_id}", "usr_missing")
    )


# Bodies that pass access checks but change nothing (missing records or failed validation).
HARMLESS: dict[tuple[str, str], Any] = {
    ("POST", "/api/v1/people/{person_id}/identifiers"): {
        "scheme": "orcid",
        "value": "x",
        "action": "add",
    },
    ("POST", "/api/v1/people/{person_id}/publications"): {"pmid": "not a pmid"},
    ("POST", "/api/v1/assertions/{assertion_id}/decision"): {"action": "nonsense"},
    ("POST", "/api/v1/assertions/decisions"): {"ids": ["asr_missing"], "action": "accept"},
    ("POST", "/api/v1/review/bulk"): {
        "person_id": "per_missing",
        "band": "strong",
        "action": "accept",
    },
    ("POST", "/api/v1/candidates/decisions"): {"ids": ["per_missing"], "action": "promote"},
    ("POST", "/api/v1/merges"): {"merge": "per_missing", "into": "per_missing2"},
    ("POST", "/api/v1/merges/{merge_id}/undo"): {},
    ("POST", "/api/v1/accounts"): {"username": "x"},
    ("POST", "/api/v1/accounts/{account_id}"): {},
    ("POST", "/api/v1/session/password"): {"current_password": "wrong", "new_password": "x"},
}
SKIP = {("POST", "/api/v1/session"), ("DELETE", "/api/v1/session")}  # tested separately


@pytest.mark.parametrize("who", list(ROLE_LEVELS))
def test_each_role_reaches_exactly_what_it_may(world: World, who: str) -> None:
    allowed_up_to = ROLE_LEVELS[who]
    for (method, path), level in sorted(POLICIES.items()):
        if (method, path) in SKIP or level == Level.PUBLIC:
            continue
        response = call(world, who, method, concrete(world, path), HARMLESS.get((method, path)))
        if level <= allowed_up_to:
            # Past the role check; a handler may still refuse a person outside the scope.
            in_scope_refusal = response.status_code == 403 and "your scope" in response.text
            assert response.status_code not in (401, 403) or in_scope_refusal, (
                who,
                method,
                path,
                response.text,
            )
        else:
            assert response.status_code == 403, (who, method, path, response.status_code)


def test_signed_out_requests_need_a_session(world: World) -> None:
    for (method, path), level in POLICIES.items():
        response = call(
            world, "anonymous", method, concrete(world, path), HARMLESS.get((method, path))
        )
        if level == Level.PUBLIC:  # reachable; asking who is signed in answers "nobody"
            assert response.status_code != 401 or path == "/api/v1/session", (method, path)
        else:
            assert response.status_code == 401, (method, path, response.status_code)
    # The web app's files are public (the sign-in page needs them); its data is not.
    assert world.clients["anonymous"].get("/").status_code == 200
    meta = world.clients["anonymous"].get("/api/v1/meta").json()["data"]
    assert meta["mode"] == {"accounts": True, "operator": None} and meta["sources"] == []


# --- scopes ------------------------------------------------------------------------------------


def test_a_unit_curator_decides_only_inside_the_unit(world: World) -> None:
    ok = call(
        world,
        "unitcur",
        "POST",
        f"/api/v1/assertions/{world.ids['inside_asr']}/decision",
        {"action": "defer"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["history"][0]["actor"] == "Unitcur"  # recorded under their name
    refused = call(
        world,
        "unitcur",
        "POST",
        f"/api/v1/assertions/{world.ids['outside_asr']}/decision",
        {"action": "defer"},
    )
    assert refused.status_code == 403
    everything = "/api/v1/review?band=strong,possible,unlikely&limit=200"
    queue = call(world, "unitcur", "GET", everything).json()["data"]
    shown = {item["person"]["id"] for item in queue}
    assert world.ids["inside"] in shown and world.ids["outside"] not in shown
    engine = make_engine(world.config.ledger_url)
    members = {
        r["subject_id"]
        for r in store.assertions_for(engine, predicate="member_of", object_id=world.ids["unit"])
    }
    engine.dispose()
    assert shown <= members  # only people of the curator's unit
    # Evidence says who may decide.
    assert (
        call(world, "unitcur", "GET", f"/api/v1/assertions/{world.ids['outside_asr']}").json()[
            "data"
        ]["can_decide"]
        is False
    )
    call(
        world,
        "unitcur",
        "POST",
        f"/api/v1/assertions/{world.ids['inside_asr']}/decision",
        {"action": "undo"},
    )


def test_a_researcher_reviews_only_their_own_suggestions(world: World) -> None:
    queue = call(world, "researcher", "GET", "/api/v1/review").json()["data"]
    assert queue and {item["person"]["id"] for item in queue} == {world.ids["researcher"]}
    own = call(
        world,
        "researcher",
        "POST",
        f"/api/v1/assertions/{world.ids['own_asr']}/decision",
        {"action": "defer"},
    )
    assert own.status_code == 200, own.text
    other = call(
        world,
        "researcher",
        "POST",
        f"/api/v1/assertions/{world.ids['inside_asr']}/decision",
        {"action": "defer"},
    )
    assert other.status_code == 403
    profile = call(world, "researcher", "GET", f"/api/v1/people/{world.ids['researcher']}").json()[
        "data"
    ]
    assert profile["can_curate"] is True
    assert (
        call(world, "researcher", "GET", f"/api/v1/people/{world.ids['inside']}").json()["data"][
            "can_curate"
        ]
        is False
    )
    call(
        world,
        "researcher",
        "POST",
        f"/api/v1/assertions/{world.ids['own_asr']}/decision",
        {"action": "undo"},
    )


def test_viewers_read_but_never_decide(world: World) -> None:
    assert call(world, "viewer", "GET", f"/api/v1/people/{world.ids['inside']}").status_code == 200
    assert (
        call(
            world,
            "viewer",
            "POST",
            f"/api/v1/assertions/{world.ids['inside_asr']}/decision",
            {"action": "accept"},
        ).status_code
        == 403
    )
    session = call(world, "viewer", "GET", "/api/v1/session").json()["data"]
    assert session["permissions"] == {
        "admin": False,
        "curate_all": False,
        "curate_any": False,
        "review": False,
    }


# --- CSRF, origins, and hosts ------------------------------------------------------------------


def test_changes_need_the_csrf_token(world: World) -> None:
    path = f"/api/v1/assertions/{world.ids['inside_asr']}/decision"
    client = world.clients["curator"]
    assert client.post(path, json={"action": "defer"}).status_code == 403
    assert (
        client.post(path, json={"action": "defer"}, headers={"X-CSRF-Token": "forged"}).status_code
        == 403
    )


def test_changes_from_other_sites_are_refused(world: World) -> None:
    path = f"/api/v1/assertions/{world.ids['inside_asr']}/decision"
    for headers in (
        {"Origin": "https://evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Origin": "null"},
    ):
        response = call(world, "curator", "POST", path, {"action": "defer"}, **headers)
        assert response.status_code == 403, headers


def test_a_reverse_proxy_keeps_the_original_host(world: World) -> None:
    path = f"/api/v1/assertions/{world.ids['inside_asr']}/decision"
    headers = {"Origin": "https://tessera.example.edu", "X-Forwarded-Host": "tessera.example.edu"}
    response = call(world, "curator", "POST", path, {"action": "defer"}, **headers)
    assert response.status_code == 200, response.text
    call(world, "curator", "POST", path, {"action": "undo"})


def test_single_operator_mode_answers_only_locally(tmp_path: Path) -> None:
    path = tmp_path / "tessera.toml"
    path.write_text(example_config_text(), encoding="utf-8")
    config = load_config(path, env={})
    DataHome(config.data.home).create()
    engine = make_engine(config.ledger_url)
    migrate.upgrade(engine)
    engine.dispose()
    with TestClient(create_app(config), base_url="http://rebound.example") as client:
        assert client.get("/api/v1/stats").status_code == 403  # DNS rebinding
    with TestClient(create_app(config), base_url=LOCAL) as client:
        session = client.get("/api/v1/session").json()["data"]
        assert session["single_operator"] and session["permissions"]["admin"]
        assert (
            client.post("/api/v1/session", json={"username": "a", "password": "b"}).status_code
            == 409
        )


# --- signing in and out ----------------------------------------------------------------------


def fresh(world: World) -> TestClient:
    return TestClient(create_app(world.config), base_url=LOCAL)


def test_sign_in_cookie_and_sign_out(world: World) -> None:
    with fresh(world) as client:
        bad = client.post(
            "/api/v1/session", json={"username": "viewer", "password": "wrong password"}
        )
        assert bad.status_code == 401
        good = client.post("/api/v1/session", json={"username": "Viewer", "password": PASSWORD})
        assert good.status_code == 200
        cookie = good.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        csrf = good.json()["data"]["csrf_token"]
        assert client.get("/api/v1/stats").status_code == 200
        assert client.delete("/api/v1/session", headers={"X-CSRF-Token": csrf}).status_code == 200
        assert client.get("/api/v1/stats").status_code == 401


def test_repeated_failures_are_throttled(world: World) -> None:
    with fresh(world) as client:
        for _ in range(5):
            client.post(
                "/api/v1/session", json={"username": "admin", "password": "not the password"}
            )
        blocked = client.post("/api/v1/session", json={"username": "admin", "password": PASSWORD})
        assert blocked.status_code == 429


def test_expired_and_revoked_sessions_end(world: World) -> None:
    engine = make_engine(world.config.ledger_url)
    with fresh(world) as client:
        client.post("/api/v1/session", json={"username": "curator", "password": PASSWORD})
        assert client.get("/api/v1/stats").status_code == 200
        with engine.begin() as conn:
            conn.execute(update(auth_session).values(expires_at=utc_now() - timedelta(seconds=1)))
        assert client.get("/api/v1/stats").status_code == 401
    engine.dispose()
    # Every other client's session just expired too; sign them back in for later tests.
    for name, client in world.clients.items():
        if name == "anonymous":
            continue
        signed = client.post("/api/v1/session", json={"username": name, "password": PASSWORD})
        world.csrf[name] = signed.json()["data"]["csrf_token"]


def test_admins_manage_accounts(world: World) -> None:
    created = call(
        world,
        "admin",
        "POST",
        "/api/v1/accounts",
        {
            "username": "newcurator",
            "name": "New Curator",
            "password": PASSWORD,
            "roles": [{"role": "curator", "scope": f"unit:{world.ids['unit_name']}"}],
        },
    )
    assert created.status_code == 201, created.text
    account = created.json()["data"]
    assert account["roles"] == [
        {
            "role": "curator",
            "scope_type": "unit",
            "scope_id": world.ids["unit"],
            "scope_label": world.ids["unit_name"],
        }
    ]
    assert "password_hash" not in account
    disabled = call(
        world, "admin", "POST", f"/api/v1/accounts/{account['id']}", {"status": "disabled"}
    )
    assert disabled.json()["data"]["status"] == "disabled"
    with fresh(world) as client:
        assert (
            client.post(
                "/api/v1/session", json={"username": "newcurator", "password": PASSWORD}
            ).status_code
            == 401
        )
    admin_id = find_account(make_engine(world.config.ledger_url), "admin")["id"]  # type: ignore[index]
    last = call(world, "admin", "POST", f"/api/v1/accounts/{admin_id}", {"status": "disabled"})
    assert last.status_code == 400 and "last active admin" in last.json()["detail"]
    assert call(world, "curator", "GET", "/api/v1/accounts").status_code == 403
