"""Single sign-on: accounts' identities at a provider, sign-ins under way, and roles that
come from the provider (wave 7).

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-08 20:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import tessera.ledger.types

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DT = tessera.ledger.types.UtcDateTime(timezone=True)


def upgrade() -> None:
    with op.batch_alter_table("role_grant", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("source", sa.String(length=16), server_default="local", nullable=False)
        )
        batch_op.create_check_constraint(
            op.f("ck_role_grant_source"), "source IN ('local', 'provider')"
        )
    op.create_table(
        "account_identity",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.String(length=40), nullable=False),
        sa.Column("issuer", sa.String(length=500), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("created_at", DT, nullable=False),
        sa.Column("last_sign_in_at", DT, nullable=True),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["account.id"],
            name=op.f("fk_account_identity_account_id_account"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_identity")),
        sa.UniqueConstraint("issuer", "subject", name=op.f("uq_account_identity_issuer_subject")),
    )
    with op.batch_alter_table("account_identity", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_account_identity_account_id"), ["account_id"], unique=False
        )
    op.create_table(
        "sign_in_attempt",
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("verifier", sa.String(length=128), nullable=False),
        sa.Column("next_path", sa.String(length=500), nullable=False),
        sa.Column("created_at", DT, nullable=False),
        sa.PrimaryKeyConstraint("state_hash", name=op.f("pk_sign_in_attempt")),
    )


def downgrade() -> None:
    op.drop_table("sign_in_attempt")
    with op.batch_alter_table("account_identity", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_account_identity_account_id"))
    op.drop_table("account_identity")
    with op.batch_alter_table("role_grant", schema=None) as batch_op:
        batch_op.drop_constraint(op.f("ck_role_grant_source"), type_="check")
        batch_op.drop_column("source")
