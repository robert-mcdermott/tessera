"""Signing in and out, changing one's password, and managing accounts (admins)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from tessera.api.access import principal_of
from tessera.api.common import engine_of, envelope
from tessera.auth import accounts
from tessera.auth.passwords import PasswordError, burn_time, verify_password
from tessera.auth.principal import Principal
from tessera.auth.sessions import COOKIE, create_session, revoke_session
from tessera.core.errors import TesseraError

LOOPBACK_ADDRESSES = frozenset({"127.0.0.1", "::1", "localhost"})
log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["access"])


def _throttle_keys(request: Request, *keys: str) -> tuple[str, ...]:
    """Throttle by account, and by address unless the client is this machine: behind a
    reverse proxy every request comes from the proxy, and one bad actor must not lock out
    everyone."""
    address = request.client.host if request.client else ""
    if address and address not in LOOPBACK_ADDRESSES:
        return (*keys, f"addr:{address}")
    return keys


def _session_body(principal: Principal, csrf_token: str | None) -> dict[str, Any]:
    return {**principal.to_json(), "csrf_token": csrf_token}


def _set_cookie(request: Request, response: Response, token: str, max_age: int) -> None:
    forwarded = request.headers.get("x-forwarded-proto", "")
    response.set_cookie(
        COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https" or forwarded == "https",
        path="/",
    )


@router.get("/session", summary="Who is signed in (401 when accounts are on and nobody is)")
def current_session(request: Request) -> dict[str, Any]:
    principal = getattr(request.state, "principal", None)
    config = request.app.state.config
    if principal is None:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return envelope(
        _session_body(principal, principal.csrf_token),
        accounts=config.server.accounts,
    )


class SignIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


@router.post("/session", summary="Sign in with a username and password")
def sign_in(request: Request, response: Response, body: SignIn) -> dict[str, Any]:
    config = request.app.state.config
    if not config.server.accounts:
        raise HTTPException(
            status_code=409, detail="Accounts are off; there is nothing to sign in to."
        )
    throttle = request.app.state.throttle
    username = body.username.strip().lower()
    keys = _throttle_keys(request, f"user:{username}")
    if throttle.blocked(*keys):
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts. Wait a few minutes and try again.",
            headers={"Retry-After": "900"},
        )
    engine = engine_of(request)
    row = accounts.find_account(engine, username)
    valid = row is not None and row["username"] == username and row["status"] == "active"
    if valid:
        valid = verify_password(body.password, row["password_hash"])
    else:
        burn_time(body.password)  # as slow as a real check, so usernames do not leak
    if not valid:
        throttle.failed(*keys)
        raise HTTPException(status_code=401, detail="That username and password do not match.")
    throttle.succeeded(*keys)
    assert row is not None
    token, csrf, expires = create_session(
        engine,
        row["id"],
        idle_hours=config.server.session_idle_hours,
        max_days=config.server.session_max_days,
        user_agent=request.headers.get("user-agent"),
    )
    _set_cookie(request, response, token, config.server.session_max_days * 86400)
    principal = Principal(
        name=row["display_name"],
        account_id=row["id"],
        username=row["username"],
        person_id=row["person_id"],
        grants=tuple(row["grants"]),
        csrf_token=csrf,
        has_password=True,
    )
    return envelope(_session_body(principal, csrf), accounts=True, expires_at=expires)


@router.delete("/session", summary="Sign out")
def sign_out(request: Request, response: Response) -> dict[str, Any]:
    revoke_session(engine_of(request), request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict")
    return envelope({"signed_out": True})


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


@router.post("/session/password", summary="Change your own password (signs out other sessions)")
def change_password(request: Request, response: Response, body: PasswordChange) -> dict[str, Any]:
    principal = principal_of(request)
    if principal.single_operator or not principal.account_id:
        raise HTTPException(status_code=409, detail="Accounts are off.")
    engine, config = engine_of(request), request.app.state.config
    throttle = request.app.state.throttle
    keys = _throttle_keys(request, f"password:{principal.account_id}")
    if throttle.blocked(*keys):
        raise HTTPException(status_code=429, detail="Too many attempts. Wait a few minutes.")
    row = accounts.find_account(engine, principal.account_id)
    assert row is not None
    if not verify_password(body.current_password, row["password_hash"]):
        throttle.failed(*keys)
        raise HTTPException(status_code=400, detail="The current password is not right.")
    throttle.succeeded(*keys)
    try:
        accounts.set_password(engine, principal.account_id, body.new_password)
    except PasswordError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    token, csrf, _ = create_session(
        engine,
        principal.account_id,
        idle_hours=config.server.session_idle_hours,
        max_days=config.server.session_max_days,
        user_agent=request.headers.get("user-agent"),
    )
    _set_cookie(request, response, token, config.server.session_max_days * 86400)
    return envelope({"changed": True, "csrf_token": csrf})


# --- accounts (admins) --------------------------------------------------------------------------


class RoleIn(BaseModel):
    role: str = Field(description="admin, curator, manager, researcher, or viewer")
    scope: str | None = Field(
        None,
        description="For curators: group:<name, address, code, or ID> or person:<ID>; none "
        "for all. For managers: group:<…> (required)",
    )
    include_below: bool = Field(False, description="A manager also manages the groups below")


class AccountIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=1024)
    email: str | None = Field(None, max_length=320)
    person_id: str | None = Field(None, max_length=40)
    roles: list[RoleIn] = Field(min_length=1, max_length=20)


class AccountChange(BaseModel):
    name: str | None = Field(None, max_length=200)
    email: str | None = Field(None, max_length=320)
    person_id: str | None = Field(None, max_length=40, description="'' to unlink")
    roles: list[RoleIn] | None = Field(None, max_length=20)
    status: str | None = Field(None, description="active or disabled")
    password: str | None = Field(None, max_length=1024, description="a new password")


def _grants(request: Request, roles: list[RoleIn]) -> list[accounts.Grant]:
    with engine_of(request).connect() as conn:
        return [accounts.make_grant(conn, r.role, r.scope, r.include_below) for r in roles]


def _fail(exc: TesseraError) -> HTTPException:
    message = str(exc)
    return HTTPException(
        status_code=404 if message.startswith("No account") else 400, detail=message
    )


@router.get("/accounts", summary="Every account and its roles (admins)")
def list_all(request: Request) -> dict[str, Any]:
    return envelope([accounts.describe(r) for r in accounts.list_accounts(engine_of(request))])


@router.post("/accounts", status_code=201, summary="Create an account (admins)")
def create(request: Request, body: AccountIn) -> dict[str, Any]:
    principal = principal_of(request)
    try:
        account_id = accounts.create_account(
            engine_of(request),
            body.username,
            body.name,
            body.password,
            _grants(request, body.roles),
            actor=principal.name,
            email=body.email,
            person_id=body.person_id or None,
        )
    except TesseraError as exc:
        status = 409 if "is taken" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    row = accounts.find_account(engine_of(request), account_id)
    assert row is not None
    return envelope(accounts.describe(row))


@router.post(
    "/accounts/{account_id}", summary="Change an account: roles, status, password (admins)"
)
def change(request: Request, account_id: str, body: AccountChange) -> dict[str, Any]:
    principal = principal_of(request)
    engine = engine_of(request)
    if accounts.find_account(engine, account_id) is None:
        raise HTTPException(status_code=404, detail="No such account.")
    try:
        if any(v is not None for v in (body.name, body.email, body.person_id, body.roles)):
            accounts.update_account(
                engine,
                account_id,
                actor=principal.name,
                grants=_grants(request, body.roles) if body.roles is not None else None,
                display_name=body.name,
                email=body.email,
                person_id="" if body.person_id is None else body.person_id,
            )
        if body.status is not None:
            accounts.set_status(engine, account_id, body.status)
        if body.password is not None:
            accounts.set_password(engine, account_id, body.password)
    except TesseraError as exc:
        raise _fail(exc) from exc
    row = accounts.find_account(engine, account_id)
    assert row is not None
    return envelope(accounts.describe(row))


# --- single sign-on (PLT-10, ADR-0011) -----------------------------------------------------------

SSO_COOKIE = "tessera_sso"
SSO_PATH = "/api/v1/auth/sso"


def _provider(request: Request) -> Any:
    """The configured provider, with its discovery document and keys kept between sign-ins."""
    from tessera.auth.oidc import Provider

    oidc = request.app.state.config.auth.oidc
    if not oidc.enabled or not request.app.state.config.server.accounts:
        raise HTTPException(status_code=404, detail="Single sign-on is not set up.")
    provider = getattr(request.app.state, "oidc_provider", None)
    if provider is None or provider.issuer != oidc.issuer:
        provider = Provider(
            oidc.issuer, transport=getattr(request.app.state, "oidc_transport", None)
        )
        request.app.state.oidc_provider = provider
    return provider


def _redirect_uri(request: Request) -> str:
    oidc = request.app.state.config.auth.oidc
    if oidc.redirect_url:
        return oidc.redirect_url
    scheme = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    host = request.headers.get("x-forwarded-host", "").split(",")[0].strip()
    scheme = scheme or request.url.scheme
    host = host or request.headers.get("host", "") or request.url.netloc
    return f"{scheme}://{host}{SSO_PATH}/callback"


def _secure(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def _to_sign_in(outcome: str) -> RedirectResponse:
    response = RedirectResponse(f"/sign-in?sso={outcome}", status_code=303)
    response.delete_cookie(SSO_COOKIE, path=SSO_PATH)
    return response


@router.get(
    "/auth/sso/start",
    summary="Begin signing in with the single sign-on provider (redirects there)",
    response_class=RedirectResponse,
    status_code=303,
)
def sso_start(request: Request, next: str | None = None) -> RedirectResponse:
    from tessera.auth import sso
    from tessera.auth.oidc import OIDCError

    provider = _provider(request)
    try:
        started = sso.begin(
            engine_of(request),
            request.app.state.config,
            provider,
            redirect_uri=_redirect_uri(request),
            next_path=next,
        )
    except OIDCError as exc:
        log.warning("Single sign-on could not begin: %s", exc)
        return _to_sign_in("failed")
    response = RedirectResponse(started.url, status_code=303)
    # Lax, not Strict: it must come back with the provider's (cross-site) redirect.
    response.set_cookie(
        SSO_COOKIE,
        started.state,
        max_age=sso.ATTEMPT_MINUTES * 60,
        httponly=True,
        samesite="lax",
        secure=_secure(request),
        path=SSO_PATH,
    )
    return response


@router.get(
    "/auth/sso/callback",
    summary="Where the single sign-on provider sends the browser back (redirects into the app)",
    response_class=RedirectResponse,
    status_code=303,
)
def sso_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    import os

    from tessera.auth import sso
    from tessera.auth.oidc import OIDCError

    provider = _provider(request)
    config = request.app.state.config
    if error:
        log.info("Single sign-on ended at the provider: %s", error[:200])
        return _to_sign_in("cancelled" if error == "access_denied" else "failed")
    engine = engine_of(request)
    try:
        account_id, next_path = sso.finish(
            engine,
            config,
            provider,
            state=state,
            browser_state=request.cookies.get(SSO_COOKIE),
            code=code,
            redirect_uri=_redirect_uri(request),
            secret=os.environ.get(config.auth.oidc.client_secret_env, ""),
        )
    except sso.SignInRefused as exc:
        log.warning("Single sign-on refused: %s", exc)
        return _to_sign_in(exc.reason)
    except OIDCError as exc:
        log.warning("Single sign-on failed: %s", exc)
        return _to_sign_in("failed")
    token, _, _ = create_session(
        engine,
        account_id,
        idle_hours=config.server.session_idle_hours,
        max_days=config.server.session_max_days,
        user_agent=request.headers.get("user-agent"),
    )
    response = RedirectResponse(next_path, status_code=303)
    _set_cookie(request, response, token, config.server.session_max_days * 86400)
    response.delete_cookie(SSO_COOKIE, path=SSO_PATH)
    return response
