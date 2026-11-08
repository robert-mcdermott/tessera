"""Who may call what: one policy per route, checked for every request.

Every API route is listed in ``POLICIES`` with the least it requires; a route missing from the
table is refused, and a test fails, so a new route cannot ship without a decision about who
may use it. Checks that depend on *which* person a request is about (a curator scoped to one
unit, a researcher reviewing their own suggestions) happen in the handler, through
``require_curate``.

Two modes:

* **Single-operator** (``server.accounts = false``): no sign-in, every request acts as the
  configured operator, and the server answers only to requests addressed to this machine.
* **Accounts**: every API route except health, metadata, and signing in needs a session
  cookie, and every change needs the session's CSRF token in a header.

In both modes a change that a browser sends from another site is refused.
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from enum import IntEnum
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from tessera.api.problems import problem
from tessera.auth.accounts import grants_of
from tessera.auth.principal import Principal
from tessera.auth.sessions import COOKIE, CSRF_HEADER, load_session
from tessera.config import LOOPBACK_HOSTS
from tessera.ledger.tables import account


class Level(IntEnum):
    PUBLIC = 0  # anyone, signed in or not
    READ = 1  # any signed-in account
    REVIEW = 2  # may decide for someone: a curator, or a researcher about themselves
    CURATE_ANY = 3  # a curator for some or all people
    CURATE_ALL = 4  # a curator for everyone
    ADMIN = 5


P, R = Level.PUBLIC, Level.READ
POLICIES: dict[tuple[str, str], Level] = {
    ("GET", "/api/v1/health"): P,
    ("GET", "/api/v1/meta"): P,
    ("GET", "/api/v1/session"): P,
    ("POST", "/api/v1/session"): P,
    ("DELETE", "/api/v1/session"): R,
    ("POST", "/api/v1/session/password"): R,
    ("GET", "/api/docs"): R,
    ("GET", "/api/openapi.json"): R,
    ("GET", "/api/v1/search"): R,
    ("GET", "/api/v1/stats"): R,
    ("GET", "/api/v1/sources"): R,
    ("GET", "/api/v1/runs"): R,
    ("GET", "/api/v1/people"): R,
    ("GET", "/api/v1/people/{person_id}"): R,
    ("GET", "/api/v1/people/{person_id}/publications"): R,
    ("GET", "/api/v1/people/{person_id}/grants"): R,
    ("GET", "/api/v1/people/{person_id}/studies"): R,
    ("GET", "/api/v1/people/{person_id}/history"): R,
    ("GET", "/api/v1/people/{person_id}/similar"): R,
    ("GET", "/api/v1/people/{person_id}/concepts"): R,
    ("GET", "/api/v1/experts"): R,
    ("GET", "/api/v1/concepts"): R,
    ("GET", "/api/v1/graph/focus/{person_id}"): R,
    ("GET", "/api/v1/graph/network"): R,
    ("GET", "/api/v1/graph/landscape"): R,
    ("GET", "/api/v1/graph/missed"): R,
    ("GET", "/api/v1/graph/units"): R,
    ("GET", "/api/v1/graph/units/pairs"): R,
    ("POST", "/api/v1/people/{person_id}/publications"): Level.REVIEW,
    ("POST", "/api/v1/people/{person_id}/identifiers"): Level.REVIEW,
    ("GET", "/api/v1/publications"): R,
    ("GET", "/api/v1/publications/{pmid}"): R,
    ("GET", "/api/v1/grants"): R,
    ("GET", "/api/v1/grants/{core}"): R,
    ("GET", "/api/v1/studies"): R,
    ("GET", "/api/v1/studies/{nct_id}"): R,
    ("GET", "/api/v1/assertions/{assertion_id}"): R,
    ("POST", "/api/v1/assertions/{assertion_id}/decision"): Level.REVIEW,
    ("POST", "/api/v1/assertions/decisions"): Level.REVIEW,
    ("GET", "/api/v1/review"): Level.REVIEW,
    ("POST", "/api/v1/review/bulk"): Level.REVIEW,
    ("GET", "/api/v1/decisions"): Level.CURATE_ANY,
    ("GET", "/api/v1/candidates"): Level.CURATE_ALL,
    ("POST", "/api/v1/candidates/decisions"): Level.CURATE_ALL,
    ("GET", "/api/v1/duplicates"): Level.CURATE_ALL,
    ("POST", "/api/v1/merges"): Level.CURATE_ANY,
    ("POST", "/api/v1/merges/{merge_id}/undo"): Level.CURATE_ANY,
    ("GET", "/api/v1/evidence"): Level.CURATE_ANY,
    ("POST", "/api/v1/evidence/evaluate"): Level.ADMIN,
    ("POST", "/api/v1/evidence/release"): Level.ADMIN,
    ("POST", "/api/v1/evidence/revert"): Level.ADMIN,
    ("POST", "/api/v1/evidence/auto-accept"): Level.ADMIN,
    ("POST", "/api/v1/evidence/auto-accept/batches/{batch_id}/undo"): Level.ADMIN,
    ("GET", "/api/v1/accounts"): Level.ADMIN,
    ("POST", "/api/v1/accounts"): Level.ADMIN,
    ("POST", "/api/v1/accounts/{account_id}"): Level.ADMIN,
}
UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _hostname(netloc: str) -> str:
    host = netloc.rsplit("@", 1)[-1]
    if host.startswith("["):  # [::1]:8000
        return host[1 : host.find("]")]
    return host.split(":", 1)[0].lower()


def _check_origin(request: Request) -> None:
    """Refuse changes a browser sends from another site (the session cookie is SameSite=Strict
    as well; this also protects single-operator mode, which has no cookie)."""
    if request.method not in UNSAFE:
        return
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="Requests from other sites are refused.")
    origin = request.headers.get("origin")
    if origin and origin != "null":
        # Behind a reverse proxy the Host may be the upstream's; the original host then comes in
        # X-Forwarded-Host (a browser cannot set that header on another site's behalf).
        hosts = {
            request.headers.get("host", "").lower(),
            request.headers.get("x-forwarded-host", "").split(",")[0].strip().lower(),
        }
        if urlsplit(origin).netloc.lower() not in hosts - {""}:
            raise HTTPException(status_code=403, detail="Requests from other sites are refused.")
    elif origin == "null":
        raise HTTPException(status_code=403, detail="Requests from other sites are refused.")


def _principal(request: Request) -> Principal | None:
    config = request.app.state.config
    if not config.server.accounts:
        return Principal(name=config.server.operator, single_operator=True)
    engine = request.app.state.engine
    session = load_session(
        engine,
        request.cookies.get(COOKIE),
        idle_hours=config.server.session_idle_hours,
        max_days=config.server.session_max_days,
    )
    if session is None:
        return None
    with engine.connect() as conn:
        row = (
            conn.execute(account.select().where(account.c.id == session["account_id"]))
            .mappings()
            .one()
        )
        grants = tuple(grants_of(conn, row["id"]))
    return Principal(
        name=row["display_name"],
        account_id=row["id"],
        username=row["username"],
        person_id=row["person_id"],
        grants=grants,
        csrf_token=session["csrf_token"],
    )


def _allowed(principal: Principal, level: Level) -> bool:
    if level <= Level.READ:
        return True
    if level == Level.REVIEW:
        return principal.reviews
    if level == Level.CURATE_ANY:
        return principal.curates_any
    if level == Level.CURATE_ALL:
        return principal.curates_all
    return principal.is_admin


PUBLIC_PATHS = frozenset(k for k, level in POLICIES.items() if level == Level.PUBLIC)


def authenticate(request: Request) -> None:
    """For every request: origin and host checks, the session, and its CSRF token.

    Runs in a middleware, so that it also covers what is not an API route (the OpenAPI
    document, files, fallbacks). Which role a route needs is ``authorize``'s job.
    """
    config = request.app.state.config
    method = "GET" if request.method == "HEAD" else request.method
    path = request.url.path
    _check_origin(request)
    if (
        not config.server.accounts
        and _hostname(request.headers.get("host", "")) not in LOOPBACK_HOSTS
    ):
        # Single-operator mode trusts this machine only; refusing other host names also
        # defeats DNS rebinding from a web page.
        raise HTTPException(
            status_code=403,
            detail="Without accounts, Tessera answers only at localhost or 127.0.0.1.",
        )
    principal = _principal(request)
    request.state.principal = principal
    if not path.startswith("/api/") or (method, path) in PUBLIC_PATHS:
        return  # the web app's files and shell hold no data; the sign-in page needs them
    if principal is None:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    if (
        config.server.accounts
        and method in UNSAFE
        and not hmac.compare_digest(
            request.headers.get(CSRF_HEADER, "").encode(), (principal.csrf_token or "").encode()
        )
    ):
        raise HTTPException(
            status_code=403, detail="This request is missing its security token; reload the page."
        )


def authorize(request: Request) -> None:
    """The app-wide dependency: the role this route needs, from ``POLICIES``."""
    route = request.scope.get("route")
    path = getattr(route, "path", request.url.path)
    method = "GET" if request.method == "HEAD" else request.method
    level = POLICIES.get((method, path))
    if level is None:
        if path.startswith("/api"):
            raise HTTPException(status_code=403, detail="This route has no access policy.")
        return  # the web app's files and shell
    if level == Level.PUBLIC:
        return
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    if not _allowed(principal, level):
        raise HTTPException(status_code=403, detail="Your role does not allow this.")


def install_access(app: FastAPI) -> None:
    """Check every request before it reaches a route, static file, or the API document.

    A middleware rather than a dependency, so that nothing (the OpenAPI document, files,
    fallbacks) can be added without passing through it. Install it before the security headers,
    so its refusals carry them too.
    """

    @app.middleware("http")
    async def access(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        try:
            await run_in_threadpool(authenticate, request)
        except HTTPException as exc:
            return problem(exc.status_code, exc.detail, instance=request.url.path)
        return await call_next(request)


def principal_of(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:  # only public routes run without one
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return principal


def require_curate(request: Request, *person_ids: str) -> Principal:
    """The principal, if they may decide for every one of these people; 403 otherwise."""
    principal = principal_of(request)
    engine = request.app.state.engine
    for person_id in person_ids:
        if not principal.can_curate(engine, person_id):
            raise HTTPException(
                status_code=403, detail="You may decide only for the people in your scope."
            )
    return principal


def require_curate_all(request: Request) -> Principal:
    principal = principal_of(request)
    if not principal.curates_all:
        raise HTTPException(status_code=403, detail="This needs a curator for everyone.")
    return principal


__all__ = [
    "POLICIES",
    "Level",
    "authenticate",
    "authorize",
    "install_access",
    "principal_of",
    "require_curate",
    "require_curate_all",
]
