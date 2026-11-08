"""Accounts and role grants in the ledger.

Roles, from least to most: **viewer** reads everything internal; **researcher** also reviews
the suggestions about their own person; **curator** decides for everyone, or only for the
people of one unit or one person when the grant has a scope; **admin** does everything,
including managing accounts. Grants add up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.engine import Connection, Engine

from tessera.auth.passwords import check_rules, hash_password
from tessera.core.clock import utc_now
from tessera.core.errors import TesseraError
from tessera.core.ids import new_id
from tessera.ledger.tables import ROLES, account, auth_session, person, role_grant, unit

USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,62}$")
SCOPED_ROLES = ("curator",)


class AccountError(TesseraError):
    """An account request that cannot be done as asked."""


@dataclass(frozen=True)
class Grant:
    role: str
    scope_type: str | None = None  # "unit" or "person"
    scope_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"role": self.role, "scope_type": self.scope_type, "scope_id": self.scope_id}


def normalize_username(username: str) -> str:
    name = (username or "").strip().lower()
    if not USERNAME.match(name):
        raise AccountError(
            "A username has 2 to 63 characters: lowercase letters, digits, dots, dashes, or "
            "underscores, starting with a letter or digit."
        )
    return name


def resolve_scope(conn: Connection, scope: str | None) -> tuple[str | None, str | None]:
    """``unit:<name, code, or ID>`` or ``person:<ID>`` to (type, ID); None for everyone."""
    if not scope:
        return None, None
    kind, _, value = scope.partition(":")
    value = value.strip()
    if kind == "unit" and value:
        found = conn.execute(
            select(unit.c.id).where(
                or_(unit.c.id == value, unit.c.code == value, unit.c.name == value)
            )
        ).scalar()
        if found is None:
            raise AccountError(f"No unit {value!r}.")
        return "unit", found
    if kind == "person" and value:
        if conn.execute(select(person.c.id).where(person.c.id == value)).scalar() is None:
            raise AccountError(f"No person {value!r}.")
        return "person", value
    raise AccountError("A scope is unit:<name, code, or ID> or person:<ID>.")


def _check_grants(conn: Connection, grants: list[Grant], person_id: str | None) -> None:
    if not grants:
        raise AccountError("An account needs at least one role.")
    for g in grants:
        if g.role not in ROLES:
            raise AccountError(f"Unknown role {g.role!r}; use {', '.join(ROLES)}.")
        if g.scope_type and g.role not in SCOPED_ROLES:
            raise AccountError(f"Only {', '.join(SCOPED_ROLES)} grants take a scope.")
        if (
            g.scope_type == "unit"
            and conn.execute(select(unit.c.id).where(unit.c.id == g.scope_id)).scalar() is None
        ):
            raise AccountError(f"No unit {g.scope_id!r}.")
        if (
            g.scope_type == "person"
            and conn.execute(select(person.c.id).where(person.c.id == g.scope_id)).scalar() is None
        ):
            raise AccountError(f"No person {g.scope_id!r}.")
        if g.role == "researcher" and not person_id:
            raise AccountError("A researcher account needs the person it belongs to.")


def create_account(
    engine: Engine,
    username: str,
    display_name: str,
    password: str,
    grants: list[Grant],
    *,
    actor: str,
    email: str | None = None,
    person_id: str | None = None,
) -> str:
    name = normalize_username(username)
    display = (display_name or "").strip() or name
    check_rules(password, username=name)
    now = utc_now()
    account_id = new_id("usr")
    with engine.begin() as conn:
        if conn.execute(select(account.c.id).where(account.c.username == name)).scalar():
            raise AccountError(f"The username {name!r} is taken.")
        if (
            person_id
            and conn.execute(select(person.c.id).where(person.c.id == person_id)).scalar() is None
        ):
            raise AccountError(f"No person {person_id!r}.")
        _check_grants(conn, grants, person_id)
        conn.execute(
            insert(account).values(
                id=account_id,
                username=name,
                display_name=display[:200],
                email=(email or "").strip().lower() or None,
                password_hash=hash_password(password),
                status="active",
                person_id=person_id,
                created_at=now,
                updated_at=now,
                password_changed_at=now,
            )
        )
        _insert_grants(conn, account_id, grants, actor)
    return account_id


def _insert_grants(conn: Connection, account_id: str, grants: list[Grant], actor: str) -> None:
    now = utc_now()
    for g in dict.fromkeys(grants):
        conn.execute(
            insert(role_grant).values(
                account_id=account_id,
                role=g.role,
                scope_type=g.scope_type,
                scope_id=g.scope_id,
                granted_by=actor,
                granted_at=now,
            )
        )


def scope_labels(conn: Connection, grants: list[Grant]) -> dict[str, str]:
    """Readable names for the units and people that grants are limited to."""
    units_ = {g.scope_id for g in grants if g.scope_type == "unit" and g.scope_id}
    people = {g.scope_id for g in grants if g.scope_type == "person" and g.scope_id}
    labels: dict[str, str] = {}
    if units_:
        labels |= dict(
            conn.execute(select(unit.c.id, unit.c.name).where(unit.c.id.in_(units_))).all()
        )
    if people:
        labels |= dict(
            conn.execute(
                select(person.c.id, person.c.display_name).where(person.c.id.in_(people))
            ).all()
        )
    return labels


def grants_of(conn: Connection, account_id: str) -> list[Grant]:
    rows = conn.execute(
        select(role_grant.c.role, role_grant.c.scope_type, role_grant.c.scope_id)
        .where(role_grant.c.account_id == account_id)
        .order_by(role_grant.c.id)
    ).all()
    return [Grant(*r) for r in rows]


def find_account(engine: Engine, key: str) -> dict[str, Any] | None:
    """An account by ID or username, with its grants."""
    with engine.connect() as conn:
        row = (
            conn.execute(
                select(account).where(or_(account.c.id == key, account.c.username == key.lower()))
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        grants = grants_of(conn, row["id"])
        return {**dict(row), "grants": grants, "labels": scope_labels(conn, grants)}


def list_accounts(engine: Engine) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = [
            dict(r) for r in conn.execute(select(account).order_by(account.c.username)).mappings()
        ]
        for r in rows:
            r["grants"] = grants_of(conn, r["id"])
            r["labels"] = scope_labels(conn, r["grants"])
        return rows


def _active_admins(conn: Connection, excluding: str | None = None) -> int:
    query = (
        select(func.count(func.distinct(account.c.id)))
        .select_from(account.join(role_grant, role_grant.c.account_id == account.c.id))
        .where(account.c.status == "active", role_grant.c.role == "admin")
    )
    if excluding:
        query = query.where(account.c.id != excluding)
    return conn.execute(query).scalar() or 0


def active_admins(engine: Engine) -> int:
    with engine.connect() as conn:
        return _active_admins(conn)


def _require(conn: Connection, account_id: str) -> dict[str, Any]:
    row = conn.execute(select(account).where(account.c.id == account_id)).mappings().first()
    if row is None:
        raise AccountError(f"No account {account_id!r}.")
    return dict(row)


def set_status(engine: Engine, account_id: str, status: str) -> None:
    """Enable or disable an account. Disabling signs it out everywhere."""
    if status not in ("active", "disabled"):
        raise AccountError("The status is active or disabled.")
    with engine.begin() as conn:
        _require(conn, account_id)
        is_admin = any(g.role == "admin" for g in grants_of(conn, account_id))
        if status == "disabled" and is_admin and not _active_admins(conn, excluding=account_id):
            raise AccountError("This is the last active admin; add another admin first.")
        conn.execute(
            update(account)
            .where(account.c.id == account_id)
            .values(status=status, updated_at=utc_now())
        )
        if status == "disabled":
            revoke_sessions(conn, account_id)


def set_password(engine: Engine, account_id: str, password: str) -> None:
    """A new password signs the account out everywhere."""
    with engine.begin() as conn:
        row = _require(conn, account_id)
        check_rules(password, username=row["username"])
        now = utc_now()
        conn.execute(
            update(account)
            .where(account.c.id == account_id)
            .values(password_hash=hash_password(password), password_changed_at=now, updated_at=now)
        )
        revoke_sessions(conn, account_id)


def update_account(
    engine: Engine,
    account_id: str,
    *,
    actor: str,
    grants: list[Grant] | None = None,
    display_name: str | None = None,
    email: str | None = None,
    person_id: str | None = "",
) -> None:
    """Change roles, name, email, or linked person. ``person_id=""`` leaves it as it is."""
    with engine.begin() as conn:
        row = _require(conn, account_id)
        new_person = row["person_id"] if person_id == "" else person_id
        values: dict[str, Any] = {"updated_at": utc_now()}
        if display_name is not None:
            values["display_name"] = display_name.strip()[:200] or row["username"]
        if email is not None:
            values["email"] = email.strip().lower() or None
        if person_id != "":
            if (
                person_id
                and conn.execute(select(person.c.id).where(person.c.id == person_id)).scalar()
                is None
            ):
                raise AccountError(f"No person {person_id!r}.")
            values["person_id"] = person_id or None
        if grants is not None:
            _check_grants(conn, grants, new_person)
            was_admin = any(g.role == "admin" for g in grants_of(conn, account_id))
            losing = was_admin and not any(g.role == "admin" for g in grants)
            if losing and row["status"] == "active" and not _active_admins(conn, account_id):
                raise AccountError("This is the last active admin; add another admin first.")
            conn.execute(delete(role_grant).where(role_grant.c.account_id == account_id))
            _insert_grants(conn, account_id, grants, actor)  # in effect from the next request
        conn.execute(update(account).where(account.c.id == account_id).values(**values))


def revoke_sessions(conn: Connection, account_id: str) -> None:
    conn.execute(
        update(auth_session)
        .where(auth_session.c.account_id == account_id, auth_session.c.revoked_at.is_(None))
        .values(revoked_at=utc_now())
    )


def describe(row: dict[str, Any]) -> dict[str, Any]:
    """An account for the API: never the password hash."""
    return {
        "id": row["id"],
        "username": row["username"],
        "name": row["display_name"],
        "email": row["email"],
        "status": row["status"],
        "person_id": row["person_id"],
        "roles": [
            {**g.to_json(), "scope_label": row.get("labels", {}).get(g.scope_id)}
            for g in row["grants"]
        ],
        "created_at": row["created_at"],
        "last_sign_in_at": row["last_sign_in_at"],
    }


__all__ = [
    "AccountError",
    "Grant",
    "active_admins",
    "create_account",
    "describe",
    "find_account",
    "grants_of",
    "list_accounts",
    "normalize_username",
    "resolve_scope",
    "set_password",
    "set_status",
    "update_account",
]
