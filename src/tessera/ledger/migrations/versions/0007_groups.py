"""Groups: kinds, several parents, addresses, sources and external identifiers, history, and
membership roles.

Units become groups. Each gains an address (``slug``), a short name, a description, a status
(active, archived, or merged into another), and the source it came from with its identifier
there. Parents move from ``unit.parent_id`` to ``unit_parent``, so a group may sit under
several others. ``unit_event`` records every change to a group and its memberships.
Memberships (``member_of``) gain a role, which also tells apart two memberships of one person
in one group; existing memberships become plain members, from the roster.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-08 21:00:00
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import tessera.ledger.types

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DT = tessera.ledger.types.UtcDateTime(timezone=True)
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

_unit = sa.table(
    "unit",
    sa.column("id", sa.String),
    sa.column("name", sa.String),
    sa.column("code", sa.String),
    sa.column("parent_id", sa.String),
    sa.column("slug", sa.String),
    sa.column("source", sa.String),
    sa.column("external_id", sa.String),
)
_parent = sa.table(
    "unit_parent",
    sa.column("child_id", sa.String),
    sa.column("parent_id", sa.String),
    sa.column("source", sa.String),
    sa.column("created_at", DT),
)
_assertion = sa.table(
    "assertion",
    sa.column("id", sa.String),
    sa.column("subject_id", sa.String),
    sa.column("object_id", sa.String),
    sa.column("predicate", sa.String),
    sa.column("method", sa.String),
    sa.column("qualifier_key", sa.String),
    sa.column("qualifiers", JSON),
    sa.column("status", sa.String),
)


def _slug(text: str) -> str:
    """The same rule as tessera.groups.store.slugify, frozen here for this migration."""
    ascii_ = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_).strip("-")[:80].strip("-") or "group"


def upgrade() -> None:
    with op.batch_alter_table("unit", schema=None) as batch_op:
        batch_op.add_column(sa.Column("slug", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("short_name", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("description", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("status", sa.String(length=16), server_default="active", nullable=False)
        )
        batch_op.add_column(sa.Column("merged_into", sa.String(length=40), nullable=True))
        batch_op.add_column(
            sa.Column("source", sa.String(length=40), server_default="manual", nullable=False)
        )
        batch_op.add_column(sa.Column("external_id", sa.String(length=200), nullable=True))
        batch_op.create_unique_constraint(batch_op.f("uq_unit_slug"), ["slug"])
        batch_op.create_unique_constraint(
            batch_op.f("uq_unit_source_external_id"), ["source", "external_id"]
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_unit_status"), "status IN ('active', 'archived', 'merged')"
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_unit_merged_into_unit"),
            "unit",
            ["merged_into"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "unit_parent",
        sa.Column("child_id", sa.String(length=40), nullable=False),
        sa.Column("parent_id", sa.String(length=40), nullable=False),
        sa.Column("source", sa.String(length=40), server_default="manual", nullable=False),
        sa.Column("created_at", DT, nullable=False),
        sa.CheckConstraint("child_id <> parent_id", name=op.f("ck_unit_parent_not_self")),
        sa.ForeignKeyConstraint(
            ["child_id"], ["unit.id"], name=op.f("fk_unit_parent_child_id_unit"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["unit.id"],
            name=op.f("fk_unit_parent_parent_id_unit"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("child_id", "parent_id", name=op.f("pk_unit_parent")),
    )
    with op.batch_alter_table("unit_parent", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_unit_parent_parent_id"), ["parent_id"], unique=False)

    op.create_table(
        "unit_event",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("unit_id", sa.String(length=40), nullable=False),
        sa.Column("actor", sa.String(length=200), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("detail", JSON, nullable=False),
        sa.Column("at", DT, nullable=False),
        sa.ForeignKeyConstraint(
            ["unit_id"], ["unit.id"], name=op.f("fk_unit_event_unit_id_unit"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_unit_event")),
    )
    with op.batch_alter_table("unit_event", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_unit_event_at"), ["at"], unique=False)
        batch_op.create_index(batch_op.f("ix_unit_event_unit_id"), ["unit_id"], unique=False)

    conn = op.get_bind()
    now = datetime.now(UTC)
    taken: set[str] = set()
    # Every unit so far came from a roster file; its code was its identifier there.
    for uid, name, code, parent in conn.execute(
        sa.select(_unit.c.id, _unit.c.name, _unit.c.code, _unit.c.parent_id).order_by(_unit.c.id)
    ).all():
        base = _slug(name)
        slug, n = base, 1
        while slug in taken:
            n += 1
            slug = f"{base}-{n}"
        taken.add(slug)
        conn.execute(
            sa.update(_unit)
            .where(_unit.c.id == uid)
            .values(slug=slug, source="roster", external_id=code)
        )
        if parent:
            conn.execute(
                sa.insert(_parent).values(
                    child_id=uid, parent_id=parent, source="manual", created_at=now
                )
            )
    for aid, method, qualifiers in conn.execute(
        sa.select(_assertion.c.id, _assertion.c.method, _assertion.c.qualifiers).where(
            _assertion.c.predicate == "member_of"
        )
    ).all():
        q = dict(qualifiers or {})
        q.setdefault("role", "member")
        q.setdefault("source", "roster" if (method or "").startswith("roster") else "manual")
        conn.execute(
            sa.update(_assertion)
            .where(_assertion.c.id == aid)
            .values(qualifier_key=q["role"], qualifiers=q)
        )

    with op.batch_alter_table("unit", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_unit_parent_id"))
        batch_op.drop_constraint(batch_op.f("fk_unit_parent_id_unit"), type_="foreignkey")
        batch_op.drop_column("parent_id")


def downgrade() -> None:
    with op.batch_alter_table("unit", schema=None) as batch_op:
        batch_op.add_column(sa.Column("parent_id", sa.String(length=40), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_unit_parent_id_unit"), "unit", ["parent_id"], ["id"], ondelete="SET NULL"
        )
        batch_op.create_index(batch_op.f("ix_unit_parent_id"), ["parent_id"], unique=False)

    conn = op.get_bind()
    first: dict[str, str] = {}
    for child, parent in conn.execute(
        sa.select(_parent.c.child_id, _parent.c.parent_id).order_by(
            _parent.c.child_id, _parent.c.created_at, _parent.c.parent_id
        )
    ).all():
        first.setdefault(child, parent)
    for child, parent in first.items():
        conn.execute(sa.update(_unit).where(_unit.c.id == child).values(parent_id=parent))
    # One membership per person and unit again: keep the one that counts most (accepted, then
    # still open, then rejected; a plain membership before a role), and move the others'
    # decisions to it rather than deleting their history.
    rows = conn.execute(
        sa.select(
            _assertion.c.id,
            _assertion.c.subject_id,
            _assertion.c.object_id,
            _assertion.c.qualifier_key,
            _assertion.c.qualifiers,
            _assertion.c.status,
        ).where(_assertion.c.predicate == "member_of")
    ).all()
    rank = {"accepted": 0, "suggested": 1, "deferred": 1, "rejected": 2}
    _event = sa.table("curation_event", sa.column("assertion_id", sa.String))
    kept: dict[tuple[str, str], str] = {}
    for aid, subject, obj, _key, _, _status in sorted(
        rows, key=lambda r: (rank.get(r[5], 1), r[3] != "member", r[0])
    ):
        if (subject, obj) in kept:
            conn.execute(
                sa.update(_event)
                .where(_event.c.assertion_id == aid)
                .values(assertion_id=kept[(subject, obj)])
            )
            conn.execute(sa.delete(_assertion).where(_assertion.c.id == aid))
        else:
            kept[(subject, obj)] = aid
    keep = set(kept.values())
    for aid, _, _, _, qualifiers, _ in rows:
        if aid not in keep:
            continue
        q = {
            k: v
            for k, v in (qualifiers or {}).items()
            if k not in {"role", "source", "external_id"}
        }
        conn.execute(
            sa.update(_assertion)
            .where(_assertion.c.id == aid)
            .values(qualifier_key="", qualifiers=q)
        )

    with op.batch_alter_table("unit_event", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_unit_event_unit_id"))
        batch_op.drop_index(batch_op.f("ix_unit_event_at"))
    op.drop_table("unit_event")
    with op.batch_alter_table("unit_parent", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_unit_parent_parent_id"))
    op.drop_table("unit_parent")
    with op.batch_alter_table("unit", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_unit_merged_into_unit"), type_="foreignkey")
        batch_op.drop_constraint(batch_op.f("ck_unit_status"), type_="check")
        batch_op.drop_constraint(batch_op.f("uq_unit_source_external_id"), type_="unique")
        batch_op.drop_constraint(batch_op.f("uq_unit_slug"), type_="unique")
        batch_op.drop_column("external_id")
        batch_op.drop_column("source")
        batch_op.drop_column("merged_into")
        batch_op.drop_column("status")
        batch_op.drop_column("description")
        batch_op.drop_column("short_name")
        batch_op.drop_column("slug")
