"""Single sign-on with OpenID Connect (PLT-10, ADR-0011), against a scripted provider."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update

from tessera.api.app import create_app
from tessera.auth import passwords
from tessera.auth.accounts import Grant, create_account
from tessera.auth.oidc import OIDCError, check_id_token, rsa_verify
from tessera.config import example_config_text, load_config
from tessera.core.clock import utc_now
from tessera.core.errors import ConfigError
from tessera.home import DataHome
from tessera.ledger import migrate
from tessera.ledger.db import make_engine
from tessera.ledger.tables import account, account_identity, person, person_identifier, role_grant
from tests.oidc_provider import (
    CLIENT_ID,
    ISSUER,
    KID,
    OPENSSL_MESSAGE,
    OPENSSL_SIGNATURE,
    SECRET,
    E,
    FakeProvider,
    N,
    sign,
)

PASSWORD = "a long test password 7"
SECRET_VAR = "OIDC_TEST_SECRET"

OIDC = f"""
[auth.oidc]
issuer = "{ISSUER}"
client_id = "{CLIENT_ID}"
client_secret_env = "{SECRET_VAR}"
label = "Sign in with Corvane"
roles_claim = "roles"
{{extra}}

[auth.oidc.roles]
"Tessera.Curator" = "curator"
"Tessera.Admin" = "admin"
"""


def _config(tmp: Path, extra: str = "") -> Path:
    text = example_config_text().replace("accounts = false", "accounts = true")
    path = tmp / "tessera.toml"
    path.write_text(text + OIDC.format(extra=extra), encoding="utf-8")
    return path


class Site:
    def __init__(self, tmp: Path, extra: str = "") -> None:
        self.path = _config(tmp, extra)
        self.config = load_config(self.path, env={})
        DataHome(self.config.data.home).create()
        self.engine = make_engine(self.config.ledger_url)
        migrate.upgrade(self.engine)
        cheap = passwords.SCRYPT_N
        passwords.SCRYPT_N = 2**10
        try:
            create_account(self.engine, "admin", "Admin", PASSWORD, [Grant("admin")], actor="t")
        finally:
            passwords.SCRYPT_N = cheap
        self.idp = FakeProvider()

    def client(self) -> TestClient:
        app = create_app(self.config)
        app.state.oidc_transport = self.idp.transport()
        return TestClient(app, base_url="http://localhost")

    def sign_in(self, client: TestClient, user: dict[str, Any], next_path: str = "/") -> Any:
        """Start, sign in at the provider as ``user``, and come back: the callback's answer."""
        started = client.get(f"/api/v1/auth/sso/start?next={next_path}", follow_redirects=False)
        assert started.status_code == 303, started.text
        location = started.headers["location"]
        assert location.startswith(f"{ISSUER}/authorize?")
        self.idp.user = user
        code = self.idp.authorize(location)
        state = parse_qs(urlsplit(location).query)["state"][0]
        return client.get(
            f"/api/v1/auth/sso/callback?code={code}&state={state}", follow_redirects=False
        )

    def grants(self, username: str) -> list[tuple[str, str | None, str]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(role_grant.c.role, role_grant.c.scope_id, role_grant.c.source)
                .join(account, account.c.id == role_grant.c.account_id)
                .where(account.c.username == username)
                .order_by(role_grant.c.role)
            ).all()
        return [tuple(r) for r in rows]


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Site]:
    monkeypatch.setenv(SECRET_VAR, SECRET)
    s = Site(tmp_path)
    yield s
    s.engine.dispose()


ADA = {
    "sub": "00u-ada",
    "preferred_username": "ada.okafor@corvane.example",
    "name": "Ada Okafor",
    "email": "ada.okafor@corvane.example",
    "email_verified": True,
    "roles": ["Tessera.Curator"],
}


# --- the flow ----------------------------------------------------------------------------------


def test_first_sign_in_creates_an_account_with_the_mapped_role(site: Site) -> None:
    with site.client() as client:
        meta = client.get("/api/v1/meta").json()["data"]
        assert meta["mode"]["sso"] == {"label": "Sign in with Corvane"}
        done = site.sign_in(client, ADA, next_path="/people?q=x")
        assert done.status_code == 303 and done.headers["location"] == "/people?q=x"
        assert "tessera_session" in done.headers.get("set-cookie", "")
        me = client.get("/api/v1/session").json()["data"]
    assert me["username"] == "ada.okafor" and me["name"] == "Ada Okafor"
    assert me["has_password"] is False and me["permissions"]["curate_all"]
    assert site.grants("ada.okafor") == [("curator", None, "provider")]
    with site.engine.connect() as conn:
        identity = conn.execute(select(account_identity)).mappings().one()
    assert identity["issuer"] == ISSUER and identity["subject"] == "00u-ada"
    assert site.idp.token_calls == 1


def test_roles_from_the_provider_are_replaced_at_each_sign_in(site: Site) -> None:
    with site.client() as client:
        site.sign_in(client, ADA)
    # An admin gives a role in Tessera; it is not the provider's to take away.
    with site.engine.begin() as conn:
        account_id = conn.execute(select(account.c.id).where(account.c.username == "ada.okafor"))
        conn.execute(insert(role_grant).values(account_id=account_id.scalar(), role="manager",
                     scope_type=None, scope_id=None, granted_by="Admin", granted_at=utc_now(),
                     source="local"))  # fmt: skip
    with site.client() as client:
        site.sign_in(client, {**ADA, "roles": []})  # the curator role was removed at the provider
    assert site.grants("ada.okafor") == [("manager", None, "local"), ("viewer", None, "provider")]
    with site.client() as client:
        site.sign_in(client, {**ADA, "roles": ["Tessera.Admin", "Tessera.Curator"]})
    assert site.grants("ada.okafor") == [
        ("admin", None, "provider"),
        ("curator", None, "provider"),
        ("manager", None, "local"),
    ]


def test_a_local_admin_still_signs_in_with_a_password(site: Site) -> None:
    with site.client() as client:
        signed = client.post("/api/v1/session", json={"username": "admin", "password": PASSWORD})
        assert signed.status_code == 200
        assert signed.json()["data"]["has_password"] is True


def test_an_account_without_a_password_cannot_sign_in_with_one(site: Site) -> None:
    with site.client() as client:
        site.sign_in(client, ADA)
    with site.client() as client:
        refused = client.post("/api/v1/session", json={"username": "ada.okafor", "password": "!"})
        assert refused.status_code == 401


@pytest.mark.parametrize(
    "tamper",
    [
        {"_bad_signature": True},
        {"aud": "another-app"},
        {"iss": "https://elsewhere.test"},
        {"exp": 1_000_000_000, "iat": 999_999_000},
        {"nonce": "not-this-one"},
        {"_header": {"alg": "HS256"}},
        {"_header": {"alg": "none"}},
        {"_key": 3},  # signed with another key
    ],
    ids=["signature", "audience", "issuer", "expired", "nonce", "hs256", "none", "key"],
)
def test_an_id_token_that_fails_a_check_signs_nobody_in(site: Site, tamper: dict[str, Any]) -> None:
    site.idp.tamper = tamper
    with site.client() as client:
        done = site.sign_in(client, ADA)
        assert done.status_code == 303 and done.headers["location"] == "/sign-in?sso=failed"
        assert client.get("/api/v1/session").status_code == 401
    with site.engine.connect() as conn:
        assert (
            conn.execute(select(account.c.id).where(account.c.username == "ada.okafor")).first()
            is None
        )


def test_the_state_belongs_to_this_browser_and_is_used_once(site: Site) -> None:
    with site.client() as client:
        started = client.get("/api/v1/auth/sso/start", follow_redirects=False)
        site.idp.user = ADA
        code = site.idp.authorize(started.headers["location"])
        state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
        with site.client() as other:  # another browser, without this one's cookie
            stolen = other.get(f"/api/v1/auth/sso/callback?code={code}&state={state}",
                               follow_redirects=False)  # fmt: skip
            assert stolen.headers["location"] == "/sign-in?sso=expired"
        done = client.get(f"/api/v1/auth/sso/callback?code={code}&state={state}",
                          follow_redirects=False)  # fmt: skip
        assert done.headers["location"] == "/"
        client.cookies.set("tessera_sso", state, path="/api/v1/auth/sso")
        again = client.get(f"/api/v1/auth/sso/callback?code={code}&state={state}",
                           follow_redirects=False)  # fmt: skip
        assert again.headers["location"] == "/sign-in?sso=expired"


def test_the_provider_refusing_or_the_person_cancelling(site: Site) -> None:
    with site.client() as client:
        client.get("/api/v1/auth/sso/start", follow_redirects=False)
        cancelled = client.get("/api/v1/auth/sso/callback?error=access_denied&state=x",
                               follow_redirects=False)  # fmt: skip
        assert cancelled.headers["location"] == "/sign-in?sso=cancelled"
    site.idp.codes.clear()
    with site.client() as client:  # the code is unknown to the provider
        started = client.get("/api/v1/auth/sso/start", follow_redirects=False)
        state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
        done = client.get(f"/api/v1/auth/sso/callback?code=nope&state={state}",
                          follow_redirects=False)  # fmt: skip
        assert done.headers["location"] == "/sign-in?sso=failed"


def test_a_wrong_client_secret_is_refused_by_the_provider(
    site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SECRET_VAR, "not-the-secret")
    with site.client() as client:
        assert site.sign_in(client, ADA).headers["location"] == "/sign-in?sso=failed"


# --- matching and linking ------------------------------------------------------------------------


def test_an_existing_account_is_matched_by_a_verified_address_only(site: Site) -> None:
    cheap = passwords.SCRYPT_N
    passwords.SCRYPT_N = 2**10
    try:
        create_account(site.engine, "aokafor", "Ada O.", PASSWORD, [Grant("viewer")], actor="t",
                       email="Ada.Okafor@corvane.example")  # fmt: skip
    finally:
        passwords.SCRYPT_N = cheap
    with site.client() as client:  # unverified: a new account, without the address
        site.sign_in(client, {**ADA, "sub": "other", "email_verified": False})
        assert client.get("/api/v1/session").json()["data"]["username"] == "ada.okafor"
    with site.client() as client:  # verified: the existing account
        site.sign_in(client, ADA)
        assert client.get("/api/v1/session").json()["data"]["username"] == "aokafor"
    assert ("curator", None, "provider") in site.grants("aokafor")


def test_trusted_addresses_match_without_being_marked_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SECRET_VAR, SECRET)
    site = Site(tmp_path, extra="trust_email = true\ncreate_accounts = false")
    with site.engine.begin() as conn:
        conn.execute(update(account).where(account.c.username == "admin")
                     .values(email="ada.okafor@corvane.example"))  # fmt: skip
    with site.client() as client:
        site.sign_in(client, {**ADA, "email_verified": None})
        assert client.get("/api/v1/session").json()["data"]["username"] == "admin"
    with site.client() as client:  # a stranger, with creating accounts turned off
        refused = site.sign_in(client, {**ADA, "sub": "x", "email": "who@else.example"})
        assert refused.headers["location"] == "/sign-in?sso=refused"
    site.engine.dispose()


def test_an_account_is_linked_to_its_person_and_may_keep_their_profile(site: Site) -> None:
    now = utc_now()
    with site.engine.begin() as conn:
        conn.execute(insert(person).values(id="per_ada", display_name="Ada Okafor",
                     status="active", created_at=now, updated_at=now))  # fmt: skip
        conn.execute(insert(person_identifier).values(person_id="per_ada", scheme="email",
                     value="ada.okafor@corvane.example", source="roster", created_at=now,
                     updated_at=now))  # fmt: skip
    with site.client() as client:
        site.sign_in(client, {**ADA, "roles": []})
        me = client.get("/api/v1/session").json()["data"]
    assert me["person_id"] == "per_ada" and me["permissions"]["review"]
    assert site.grants("ada.okafor") == [
        ("researcher", None, "provider"),
        ("viewer", None, "provider"),
    ]


def test_a_disabled_account_is_refused(site: Site) -> None:
    with site.client() as client:
        site.sign_in(client, ADA)
    with site.engine.begin() as conn:
        conn.execute(
            update(account).where(account.c.username == "ada.okafor").values(status="disabled")
        )
    with site.client() as client:
        assert site.sign_in(client, ADA).headers["location"] == "/sign-in?sso=refused"


def test_without_a_default_role_someone_with_no_mapped_role_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SECRET_VAR, SECRET)
    site = Site(tmp_path, extra='default_role = ""\nresearchers = false')
    with site.client() as client:
        assert (
            site.sign_in(client, {**ADA, "roles": []}).headers["location"] == "/sign-in?sso=refused"
        )
        assert site.sign_in(client, ADA).headers["location"] == "/"
    site.engine.dispose()


def test_the_last_admin_is_never_removed_by_the_provider(site: Site) -> None:
    with site.client() as client:
        site.sign_in(client, {**ADA, "roles": ["Tessera.Admin"]})
    with site.engine.begin() as conn:  # the local admin leaves
        conn.execute(update(account).where(account.c.username == "admin").values(status="disabled"))
    with site.client() as client:
        site.sign_in(client, {**ADA, "roles": []})
    assert ("admin", None, "provider") in site.grants("ada.okafor")


def test_a_mapped_role_can_be_limited_to_a_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tessera.groups import store as groups

    monkeypatch.setenv(SECRET_VAR, SECRET)
    site = Site(tmp_path, extra="")
    site.path.write_text(site.path.read_text() + '"lab-managers" = "manager=group:okafor-lab"\n')
    site.config = load_config(site.path, env={})
    with site.engine.begin() as conn:
        unit_id = groups.create_group(conn, {"name": "Okafor Lab", "kind": "lab"}, actor="t",
                                      vocabulary=site.config.vocabulary)  # fmt: skip
    with site.client() as client:
        site.sign_in(client, {**ADA, "roles": ["lab-managers"]})
    assert site.grants("ada.okafor") == [("manager", unit_id, "provider")]
    site.engine.dispose()


def test_single_sign_on_is_absent_until_configured(client: TestClient) -> None:
    assert client.get("/api/v1/auth/sso/start", follow_redirects=False).status_code == 404
    assert client.get("/api/v1/meta").json()["data"]["mode"]["sso"] is None


# --- the pieces ----------------------------------------------------------------------------------


def test_rsa_verification_agrees_with_openssl() -> None:
    assert rsa_verify("RS256", N, E, OPENSSL_MESSAGE.encode(), OPENSSL_SIGNATURE)
    assert not rsa_verify("RS256", N, E, OPENSSL_MESSAGE.encode() + b".", OPENSSL_SIGNATURE)
    assert not rsa_verify("RS384", N, E, OPENSSL_MESSAGE.encode(), OPENSSL_SIGNATURE)
    assert not rsa_verify("RS256", N, E, OPENSSL_MESSAGE.encode(), OPENSSL_SIGNATURE[:-1])
    assert not rsa_verify("RS256", 2**1023 + 1, E, b"x", b"\x01" * 128)  # too short a key


def test_id_token_checks() -> None:
    import time

    from tests.oidc_provider import b64

    key = {"kty": "RSA", "kid": KID, "n": b64(N.to_bytes(256, "big")), "e": "AQAB"}
    now = int(time.time())
    good = {"iss": ISSUER, "aud": [CLIENT_ID, "other"], "azp": CLIENT_ID, "sub": "s",
            "iat": now, "exp": now + 60, "nonce": "n"}  # fmt: skip
    token = sign({"alg": "RS256", "kid": KID}, good)
    assert check_id_token(token, [key], issuer=ISSUER, client_id=CLIENT_ID, nonce="n")["sub"] == "s"
    bad = sign({"alg": "RS256", "kid": KID}, {**good, "azp": "other"})
    with pytest.raises(OIDCError, match="authorized party"):
        check_id_token(bad, [key], issuer=ISSUER, client_id=CLIENT_ID, nonce="n")
    with pytest.raises(OIDCError, match="signature"):
        check_id_token(token, [{**key, "kid": "elsewhere"}], issuer=ISSUER, client_id=CLIENT_ID,
                       nonce="n")  # fmt: skip
    with pytest.raises(OIDCError, match="well-formed"):
        check_id_token("not.a-token", [key], issuer=ISSUER, client_id=CLIENT_ID, nonce="n")


def test_the_configuration_is_checked(tmp_path: Path) -> None:
    base = f'[auth.oidc]\nissuer = "{ISSUER}"\nclient_id = "x"\n'

    def problems(extra: str, accounts: bool = True) -> list[str]:
        text = example_config_text()
        if accounts:
            text = text.replace("accounts = false", "accounts = true")
        path = tmp_path / "t.toml"
        path.write_text(text + extra, encoding="utf-8")
        try:
            load_config(path, env={})
        except ConfigError as exc:
            return exc.problems
        return []

    assert problems(f'[auth.oidc]\nissuer = "{ISSUER}"\nclient_id = "x"\n') == []
    assert any(
        "needs accounts" in p
        for p in problems(f'[auth.oidc]\nissuer = "{ISSUER}"\nclient_id = "x"\n', accounts=False)
    )
    assert any(
        "https://" in p
        for p in problems('[auth.oidc]\nissuer = "http://idp.example.org"\nclient_id = "x"\n')
    )
    assert (
        problems('[auth.oidc]\nissuer = "http://localhost:8080/realms/t"\nclient_id = "x"\n') == []
    )
    assert any("client_id" in p for p in problems(f'[auth.oidc]\nissuer = "{ISSUER}"\n'))
    assert any(
        "never written" in p
        for p in problems(
            f'[auth.oidc]\nissuer = "{ISSUER}"\nclient_id = "x"\nclient_secret = "s"\n'
        )
    )
    assert any(
        "not a role" in p
        for p in problems(base + 'roles_claim = "roles"\n[auth.oidc.roles]\nboss = "boss"\n')
    )
    assert any("roles_claim" in p for p in problems(base + '[auth.oidc.roles]\nboss = "admin"\n'))


def test_an_admin_edits_only_the_roles_given_in_tessera(site: Site) -> None:
    with site.client() as client:
        site.sign_in(client, ADA)
    with site.client() as admin:
        signed = admin.post("/api/v1/session", json={"username": "admin", "password": PASSWORD})
        headers = {"X-CSRF-Token": signed.json()["data"]["csrf_token"]}
        listed = {a["username"]: a for a in admin.get("/api/v1/accounts").json()["data"]}
        ada = listed["ada.okafor"]
        assert ada["sign_in"]["single_sign_on"] and not ada["sign_in"]["password"]
        assert listed["admin"]["sign_in"] == {
            "password": True, "single_sign_on": False, "single_sign_on_at": None,
        }  # fmt: skip
        assert [(r["role"], r["source"]) for r in ada["roles"]] == [("curator", "provider")]
        saved = admin.post(f"/api/v1/accounts/{ada['id']}", json={"roles": [{"role": "viewer"}]},
                           headers=headers)  # fmt: skip
        assert saved.status_code == 200, saved.text
        cleared = admin.post(f"/api/v1/accounts/{ada['id']}", json={"roles": []}, headers=headers)
        assert cleared.status_code == 200, cleared.text  # the provider's role remains
    assert site.grants("ada.okafor") == [("curator", None, "provider")]


def test_a_rotated_signing_key_is_read_again() -> None:
    import time

    import httpx

    from tessera.auth.oidc import Provider
    from tests.oidc_provider import b64

    served = {"kid": "old"}
    calls = {"jwks": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jwks":
            calls["jwks"] += 1
            key = {
                "kty": "RSA",
                "kid": served["kid"],
                "n": b64(N.to_bytes(256, "big")),
                "e": "AQAB",
            }
            return httpx.Response(200, json={"keys": [key]})
        return httpx.Response(200, json=FakeProvider().metadata())

    provider = Provider(ISSUER, transport=httpx.MockTransport(handle))
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "s",
        "iat": now,
        "exp": now + 60,
        "nonce": "n",
    }
    provider.verify(sign({"alg": "RS256", "kid": "old"}, claims), client_id=CLIENT_ID, nonce="n")
    served["kid"] = "new"  # the provider rotates its key
    token = sign({"alg": "RS256", "kid": "new"}, claims)
    assert provider.verify(token, client_id=CLIENT_ID, nonce="n")["sub"] == "s"
    assert calls["jwks"] == 2
