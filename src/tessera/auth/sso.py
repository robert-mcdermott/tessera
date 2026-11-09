"""Single sign-on: beginning a sign-in, finishing it, and the account it signs in (PLT-10).

A sign-in begins with a state value, a nonce, and a PKCE verifier, kept in the ledger by a hash
of the state for ten minutes and used once. It finishes when the provider sends the browser back
with a code: the state must match the one in the browser's cookie, the code is exchanged for
tokens, and the ID token is checked (``tessera.auth.oidc``). The account is then found by the
provider's subject identifier, matched by a verified email address, or created; it is linked to
the person in the directory with the same email address; and the roles the provider maps are
put in place of those it mapped before. Roles given in Tessera are never touched.
"""

from __future__ import annotations

import hmac
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Connection, Engine

from tessera.auth.accounts import USERNAME, AccountError, Grant, grants_of, make_grant
from tessera.auth.oidc import PKCE, OIDCError, Provider
from tessera.auth.sessions import NO_PASSWORD, token_hash
from tessera.config import Config
from tessera.core.clock import utc_now
from tessera.core.errors import TesseraError
from tessera.core.ids import new_id
from tessera.ledger.tables import (
    account,
    account_identity,
    person,
    person_identifier,
    role_grant,
    sign_in_attempt,
)

log = logging.getLogger(__name__)

ATTEMPT_MINUTES = 10
GRANTED_BY = "single sign-on"


class SignInRefused(TesseraError):
    """The provider signed the person in, but Tessera will not: ``reason`` says why."""

    def __init__(self, message: str, reason: str = "refused") -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class Started:
    state: str
    url: str


def safe_next(path: str | None) -> str:
    """Where to go after signing in: a path on this site only."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return "/"
    if path.startswith("/sign-in"):
        return "/"
    return path[:500]


def begin(engine: Engine, config: Config, provider: Provider, *, redirect_uri: str,
          next_path: str | None) -> Started:  # fmt: skip
    oidc = config.auth.oidc
    state, nonce, pkce = secrets.token_urlsafe(32), secrets.token_urlsafe(32), PKCE.new()
    url = provider.authorization_url(
        client_id=oidc.client_id,
        redirect_uri=redirect_uri,
        scopes=oidc.scopes,
        state=state,
        nonce=nonce,
        challenge=pkce.challenge,
    )
    now = utc_now()
    with engine.begin() as conn:
        conn.execute(
            delete(sign_in_attempt).where(
                sign_in_attempt.c.created_at < now - timedelta(minutes=ATTEMPT_MINUTES)
            )
        )
        conn.execute(
            insert(sign_in_attempt).values(
                state_hash=token_hash(state),
                nonce=nonce,
                verifier=pkce.verifier,
                next_path=safe_next(next_path),
                created_at=now,
            )
        )
    return Started(state, url)


def finish(engine: Engine, config: Config, provider: Provider, *, state: str | None,
           browser_state: str | None, code: str | None, redirect_uri: str,
           secret: str) -> tuple[str, str]:  # fmt: skip
    """(account ID, where to go next) for a provider's callback; raises ``SignInRefused`` or
    ``OIDCError``."""
    if not state or not browser_state or not hmac.compare_digest(state, browser_state):
        raise SignInRefused("This sign-in was not started in this browser.", "expired")
    with engine.begin() as conn:
        row = (
            conn.execute(
                select(sign_in_attempt).where(sign_in_attempt.c.state_hash == token_hash(state))
            )
            .mappings()
            .first()
        )
        conn.execute(
            delete(sign_in_attempt).where(sign_in_attempt.c.state_hash == token_hash(state))
        )
    if row is None or row["created_at"] < utc_now() - timedelta(minutes=ATTEMPT_MINUTES):
        raise SignInRefused("This sign-in has expired or was already used.", "expired")
    if not code:
        raise OIDCError("The provider sent no authorization code.")
    oidc = config.auth.oidc
    tokens = provider.exchange(
        code=code,
        redirect_uri=redirect_uri,
        verifier=row["verifier"],
        client_id=oidc.client_id,
        secret=secret,
    )
    claims = provider.verify(tokens["id_token"], client_id=oidc.client_id, nonce=row["nonce"])
    extra = provider.userinfo(str(tokens.get("access_token") or ""))
    if extra.get("sub") == claims["sub"]:
        claims = {**extra, **claims}  # the checked ID token wins
    issuer = provider.metadata()["issuer"]
    return sign_in_account(engine, config, issuer, claims), row["next_path"]


# --- the account ------------------------------------------------------------------------------


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value if isinstance(v, str | int)]
    return []


def _username(conn: Connection, claims: dict[str, Any], claim: str, email: str) -> str:
    raw = _text(claims.get(claim)) or email or _text(claims.get("sub"))
    base = re.sub(r"[^a-z0-9._-]+", ".", raw.split("@", 1)[0].lower()).strip("._-")[:56]
    if not USERNAME.match(base):
        base = f"user.{secrets.token_hex(3)}"
    name, n = base, 1
    while conn.execute(select(account.c.id).where(account.c.username == name)).first():
        n += 1
        name = f"{base}-{n}"
    return name


def _person_by_email(conn: Connection, email: str) -> str | None:
    found = {
        r[0]
        for r in conn.execute(
            select(person.c.id)
            .join(person_identifier, person_identifier.c.person_id == person.c.id)
            .where(
                person_identifier.c.scheme == "email",
                func.lower(person_identifier.c.value) == email,
                person.c.status.in_(("active", "former")),
            )
        )
    }
    return found.pop() if len(found) == 1 else None


def _mapped(conn: Connection, config: Config, claims: dict[str, Any]) -> list[Grant]:
    oidc = config.auth.oidc
    if not oidc.roles_claim:
        return []
    values = set(_values(claims.get(oidc.roles_claim)))
    grants = []
    for value, spec in oidc.roles:
        if value not in values:
            continue
        role, _, scope = spec.partition("=")
        try:
            grants.append(make_grant(conn, role.strip().lower(), scope.strip() or None))
        except AccountError as exc:
            log.warning("auth.oidc.roles.%s: %s (the role is left out)", value, exc)
    return grants


def _admins_besides(conn: Connection, account_id: str) -> int:
    return int(
        conn.execute(
            select(func.count(func.distinct(account.c.id)))
            .join(role_grant, role_grant.c.account_id == account.c.id)
            .where(
                role_grant.c.role == "admin",
                account.c.status == "active",
                account.c.id != account_id,
            )
        ).scalar()
        or 0
    )


def _set_provider_grants(conn: Connection, config: Config, account_id: str,
                         person_id: str | None, claims: dict[str, Any]) -> None:  # fmt: skip
    oidc = config.auth.oidc
    wanted = _mapped(conn, config, claims)
    if (
        not wanted
        and oidc.default_role
        and not (oidc.default_role == "researcher" and not person_id)
    ):
        wanted = [Grant(oidc.default_role)]
    if oidc.researchers and person_id:
        wanted.append(Grant("researcher"))
    wanted = [g for g in dict.fromkeys(wanted) if g.role != "researcher" or person_id]
    before = conn.execute(
        select(role_grant.c.role).where(
            role_grant.c.account_id == account_id, role_grant.c.source == "provider"
        )
    ).all()
    if (
        any(r[0] == "admin" for r in before)
        and not any(g.role == "admin" and not g.scope_type for g in wanted)
        and _admins_besides(conn, account_id) == 0
    ):
        log.warning("Single sign-on would remove the last admin; the admin role is kept.")
        wanted.append(Grant("admin"))
    conn.execute(
        delete(role_grant).where(
            role_grant.c.account_id == account_id, role_grant.c.source == "provider"
        )
    )
    now = utc_now()
    for g in wanted:
        conn.execute(
            insert(role_grant).values(
                account_id=account_id,
                role=g.role,
                scope_type=g.scope_type,
                scope_id=g.scope_id,
                include_below=g.include_below,
                granted_by=GRANTED_BY,
                granted_at=now,
                source="provider",
            )
        )
    if not grants_of(conn, account_id):
        raise SignInRefused("Your account has no role in Tessera.")


def sign_in_account(engine: Engine, config: Config, issuer: str, claims: dict[str, Any]) -> str:
    """The account the provider's claims sign in, found, matched, or created, with its roles
    from the provider brought up to date."""
    oidc = config.auth.oidc
    subject = str(claims["sub"])
    email = _text(claims.get(oidc.email_claim)).lower()
    email = email if "@" in email else ""
    verified = claims.get("email_verified") in (True, "true")
    trusted = bool(email) and (verified or oidc.trust_email)
    name = _text(claims.get(oidc.name_claim)) or _text(claims.get(oidc.username_claim)) or email
    now = utc_now()
    with engine.begin() as conn:
        found = conn.execute(
            select(account_identity.c.account_id).where(
                account_identity.c.issuer == issuer, account_identity.c.subject == subject
            )
        ).scalar()
        if found is None and trusted:
            matches = [
                r[0]
                for r in conn.execute(
                    select(account.c.id).where(func.lower(account.c.email) == email)
                )
            ]
            if len(matches) > 1:
                raise SignInRefused(
                    f"More than one account has the address {email}; an administrator must "
                    "give each its own."
                )
            found = matches[0] if matches else None
        if found is None and not oidc.create_accounts:
            raise SignInRefused(f"There is no account for {email or name or subject}.")
        if found is None:
            found = new_id("usr")
            conn.execute(
                insert(account).values(
                    id=found,
                    username=_username(conn, claims, oidc.username_claim, email),
                    display_name=(name or "New account")[:200],
                    email=email if trusted else None,
                    password_hash=NO_PASSWORD,
                    status="active",
                    created_at=now,
                    updated_at=now,
                    password_changed_at=now,
                )
            )
            log.info("Single sign-on created an account for %s", email or subject)
        row = conn.execute(select(account).where(account.c.id == found)).mappings().one()
        if row["status"] != "active":
            raise SignInRefused("This account is disabled.")
        if not conn.execute(
            select(account_identity.c.id).where(
                account_identity.c.issuer == issuer, account_identity.c.subject == subject
            )
        ).first():
            conn.execute(
                insert(account_identity).values(
                    account_id=found, issuer=issuer, subject=subject, email=email or None,
                    created_at=now,
                )
            )  # fmt: skip
        person_id = row["person_id"]
        if person_id is None and oidc.link_people and trusted:
            person_id = _person_by_email(conn, email)
        changes: dict[str, Any] = {"person_id": person_id}
        if name:
            changes["display_name"] = name[:200]
        if trusted:
            changes["email"] = email
        conn.execute(update(account).where(account.c.id == found).values(**changes, updated_at=now))
        conn.execute(
            update(account_identity)
            .where(account_identity.c.issuer == issuer, account_identity.c.subject == subject)
            .values(last_sign_in_at=now, email=email or None)
        )
        _set_provider_grants(conn, config, found, person_id, claims)
    return found


__all__ = ["SignInRefused", "Started", "begin", "finish", "safe_next", "sign_in_account"]
