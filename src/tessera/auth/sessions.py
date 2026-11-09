"""Browser sessions: an opaque random token in an HttpOnly cookie, stored only as a hash.

A session lasts ``idle_hours`` past its last use and never longer than ``max_days`` from
sign-in. Each session has its own CSRF token, which the web app sends back in a header with
every change it asks for.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Engine

from tessera.core.clock import utc_now
from tessera.ledger.tables import account, auth_session, failed_sign_in

COOKIE = "tessera_session"
CSRF_HEADER = "X-CSRF-Token"
TOUCH_EVERY = timedelta(minutes=1)  # write the sliding expiry at most this often


NO_PASSWORD = "!"  # noqa: S105 (an account without one; never matches a password)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(
    engine: Engine,
    account_id: str,
    *,
    idle_hours: int,
    max_days: int,
    user_agent: str | None = None,
) -> tuple[str, str, datetime]:
    """A new session for a signed-in account: (token, CSRF token, expiry)."""
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    now = utc_now()
    expires = min(now + timedelta(hours=idle_hours), now + timedelta(days=max_days))
    with engine.begin() as conn:
        conn.execute(
            insert(auth_session).values(
                token_hash=token_hash(token),
                account_id=account_id,
                csrf_token=csrf,
                created_at=now,
                last_seen_at=now,
                expires_at=expires,
                user_agent=(user_agent or "")[:300] or None,
            )
        )
        conn.execute(update(account).where(account.c.id == account_id).values(last_sign_in_at=now))
    return token, csrf, expires


def load_session(
    engine: Engine, token: str | None, *, idle_hours: int, max_days: int
) -> dict[str, Any] | None:
    """The live session for a token, with its account, sliding its expiry; None if none."""
    if not token or len(token) > 200:
        return None
    now = utc_now()
    key = token_hash(token)
    with engine.begin() as conn:
        row = (
            conn.execute(
                select(auth_session, account.c.status.label("account_status"))
                .join(account, account.c.id == auth_session.c.account_id)
                .where(auth_session.c.token_hash == key)
            )
            .mappings()
            .first()
        )
        if (
            row is None
            or row["revoked_at"] is not None
            or row["expires_at"] <= now
            or row["account_status"] != "active"
        ):
            return None
        if now - row["last_seen_at"] >= TOUCH_EVERY:
            expires = min(
                now + timedelta(hours=idle_hours), row["created_at"] + timedelta(days=max_days)
            )
            conn.execute(
                update(auth_session)
                .where(auth_session.c.token_hash == key)
                .values(last_seen_at=now, expires_at=expires)
            )
        return dict(row)


def revoke_session(engine: Engine, token: str | None) -> None:
    if not token:
        return
    with engine.begin() as conn:
        conn.execute(
            update(auth_session)
            .where(
                auth_session.c.token_hash == token_hash(token), auth_session.c.revoked_at.is_(None)
            )
            .values(revoked_at=utc_now())
        )


class SignInThrottle:
    """Slow down password guessing: after ``limit`` failures in ``window`` seconds for one
    account or one address, further attempts are refused until the window passes.

    Failures are kept in the ledger rather than in memory, so the limit holds however many
    server processes there are, and across restarts. Only a hash of each key is stored."""

    def __init__(
        self,
        limit: int = 5,
        window: float = 900.0,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.limit, self.window, self.clock = limit, timedelta(seconds=window), clock

    @staticmethod
    def _hashes(keys: tuple[str, ...]) -> list[str]:
        return [token_hash(k) for k in keys]

    def blocked(self, engine: Engine, *keys: str) -> bool:
        if not keys:
            return False
        cutoff = self.clock() - self.window
        with engine.connect() as conn:
            counts = conn.execute(
                select(func.count())
                .select_from(failed_sign_in)
                .where(
                    failed_sign_in.c.key_hash.in_(self._hashes(keys)),
                    failed_sign_in.c.failed_at >= cutoff,
                )
                .group_by(failed_sign_in.c.key_hash)
            ).scalars()
            return any(n >= self.limit for n in counts)

    def failed(self, engine: Engine, *keys: str) -> None:
        if not keys:
            return
        now = self.clock()
        with engine.begin() as conn:
            conn.execute(
                delete(failed_sign_in).where(failed_sign_in.c.failed_at < now - self.window)
            )
            conn.execute(
                insert(failed_sign_in),
                [{"key_hash": h, "failed_at": now} for h in self._hashes(keys)],
            )

    def succeeded(self, engine: Engine, *keys: str) -> None:
        if not keys:
            return
        with engine.begin() as conn:
            conn.execute(
                delete(failed_sign_in).where(failed_sign_in.c.key_hash.in_(self._hashes(keys)))
            )


__all__ = [
    "COOKIE",
    "CSRF_HEADER",
    "SignInThrottle",
    "create_session",
    "load_session",
    "revoke_session",
    "token_hash",
]
