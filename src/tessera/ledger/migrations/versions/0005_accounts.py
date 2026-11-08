"""Local accounts: account, role_grant, and auth_session (PLT-06, CUR-06).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-08 15:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import tessera.ledger.types

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DT = tessera.ledger.types.UtcDateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "account",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("person_id", sa.String(length=40), nullable=True),
        sa.Column("created_at", DT, nullable=False),
        sa.Column("updated_at", DT, nullable=False),
        sa.Column("password_changed_at", DT, nullable=False),
        sa.Column("last_sign_in_at", DT, nullable=True),
        sa.CheckConstraint("status IN ('active', 'disabled')", name=op.f("ck_account_status")),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["person.id"],
            name=op.f("fk_account_person_id_person"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account")),
        sa.UniqueConstraint("username", name=op.f("uq_account_username")),
    )
    op.create_table(
        "role_grant",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.String(length=40), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=True),
        sa.Column("scope_id", sa.String(length=40), nullable=True),
        sa.Column("granted_by", sa.String(length=200), nullable=False),
        sa.Column("granted_at", DT, nullable=False),
        sa.CheckConstraint(
            "role IN ('admin', 'curator', 'researcher', 'viewer')", name=op.f("ck_role_grant_role")
        ),
        sa.CheckConstraint(
            "(scope_type IS NULL AND scope_id IS NULL) OR "
            "(scope_type IN ('unit', 'person') AND scope_id IS NOT NULL)",
            name=op.f("ck_role_grant_scope"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["account.id"],
            name=op.f("fk_role_grant_account_id_account"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_grant")),
    )
    with op.batch_alter_table("role_grant", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_role_grant_account_id"), ["account_id"], unique=False)
    op.create_table(
        "auth_session",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.String(length=40), nullable=False),
        sa.Column("csrf_token", sa.String(length=64), nullable=False),
        sa.Column("created_at", DT, nullable=False),
        sa.Column("last_seen_at", DT, nullable=False),
        sa.Column("expires_at", DT, nullable=False),
        sa.Column("revoked_at", DT, nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["account.id"],
            name=op.f("fk_auth_session_account_id_account"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("token_hash", name=op.f("pk_auth_session")),
    )
    with op.batch_alter_table("auth_session", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_auth_session_account_id"), ["account_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("auth_session", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_auth_session_account_id"))
    op.drop_table("auth_session")
    with op.batch_alter_table("role_grant", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_role_grant_account_id"))
    op.drop_table("role_grant")
    op.drop_table("account")
