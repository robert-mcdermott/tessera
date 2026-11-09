"""Going back (for development; operators restore a backup instead) loses nothing it can keep,
and never turns a provider's roles into local ones (wave 8's review of the migrations)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import insert, select

from tessera.auth import passwords
from tessera.auth.accounts import Grant, create_account
from tessera.core.clock import utc_now
from tessera.evidence import store
from tessera.evidence.names import make_name
from tessera.ledger import migrate
from tessera.ledger.db import make_engine
from tessera.ledger.tables import assertion, curation_event, role_grant, unit


def ledger(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = make_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
    migrate.upgrade(engine)
    return engine


def test_going_back_past_single_sign_on_drops_the_providers_roles(tmp_path: Path) -> None:
    engine = ledger(tmp_path)
    cheap, passwords.SCRYPT_N = passwords.SCRYPT_N, 2**10
    try:
        account_id = create_account(engine, "pat", "Pat", "a long enough passphrase",
                                    [Grant("viewer")], actor="test")  # fmt: skip
    finally:
        passwords.SCRYPT_N = cheap
    with engine.begin() as conn:
        conn.execute(insert(role_grant).values(account_id=account_id, role="admin",
                                               source="provider", granted_by="sso",
                                               granted_at=utc_now()))  # fmt: skip
    migrate.downgrade(engine, "0022")
    migrate.upgrade(engine)
    with engine.connect() as conn:
        roles = set(conn.execute(select(role_grant.c.role, role_grant.c.source)
                                 .where(role_grant.c.account_id == account_id)).all())  # fmt: skip
    assert roles == {("viewer", "local")}  # admin comes back only from the provider
    engine.dispose()


def test_going_back_past_groups_keeps_the_membership_that_counts(tmp_path: Path) -> None:
    engine = ledger(tmp_path)
    now = utc_now()
    with engine.begin() as conn:
        pid = store.create_person(conn, make_name("Okafor", "Ada"), status="active",
                                  source="roster")  # fmt: skip
        conn.execute(insert(unit).values(id="unt_LAB", name="Okafor Lab", slug="okafor-lab",
                                         kind="lab", created_at=now, updated_at=now))  # fmt: skip
        for aid, role, status in (("asr_0A", "staff", "rejected"),
                                  ("asr_0B", "postdoc", "accepted")):  # fmt: skip
            conn.execute(insert(assertion).values(
                id=aid, subject_type="person", subject_id=pid, predicate="member_of",
                object_type="unit", object_id="unt_LAB", qualifier_key=role,
                qualifiers={"role": role, "source": "roster"}, origin="source",
                method="roster/v1", evidence=[], status=status, locked=True, reported=True,
                first_seen_at=now, last_evaluated_at=now, updated_at=now))  # fmt: skip
            conn.execute(insert(curation_event).values(assertion_id=aid, actor="cur",
                                                       action="reject" if status == "rejected"
                                                       else "accept", at=now))  # fmt: skip
    migrate.downgrade(engine, "0006")
    with engine.connect() as conn:
        left = conn.execute(select(assertion.c.id, assertion.c.status)
                            .where(assertion.c.predicate == "member_of")).all()  # fmt: skip
        events = conn.execute(select(curation_event.c.assertion_id)).scalars().all()
    assert left == [("asr_0B", "accepted")]  # not the lower ID, which was rejected
    assert events == ["asr_0B", "asr_0B"]  # the other's history moved, not lost
    engine.dispose()


def test_a_scope_names_one_group_or_asks_which(tmp_path: Path) -> None:
    import pytest

    from tessera.auth.accounts import AccountError, resolve_scope

    engine = ledger(tmp_path)
    now = utc_now()
    with engine.begin() as conn:
        for uid, slug in (("unt_A", "imaging-core-1"), ("unt_B", "imaging-core-2")):
            values = {"name": "Imaging Core", "kind": "core", "created_at": now, "updated_at": now}
            conn.execute(insert(unit).values(id=uid, slug=slug, **values))
        with pytest.raises(AccountError, match="imaging-core-1, imaging-core-2"):
            resolve_scope(conn, "group:Imaging Core")
        assert resolve_scope(conn, "group:imaging-core-2") == ("unit", "unt_B")
    engine.dispose()
