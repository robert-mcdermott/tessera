"""Reading links and the review queue without the evidence, at scale (wave 7).

Covering indexes, so that a person's links (for lists and Insight figures), a record's links, and
the review queue's open suggestions are read from an index alone: the evidence column, before
status and score, spills each row into overflow pages. A record's index has status and reported
too, so that SQLite, with no statistics, prefers it to the queue's for a record's links. And
``assertion.review_flags``, what the queue orders by besides the score ("conflict",
"ambiguous"), filled in here from the evidence already kept.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-08 21:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LINKS = ["subject_id", "predicate", "status", "reported", "score", "object_id", "id"]
QUEUE = ["predicate", "status", "reported", "score", "subject_id", "review_flags", "id"]
OBJECTS = ["object_id", "predicate", "status", "reported", "subject_id", "id"]
CONFLICTS = (
    "slot_conflict",
    "middle_conflict",
    "given_conflict",
    "orcid_conflict",
    "orcid_rejected",
    "orcid_inconsistent",
)


def upgrade() -> None:
    with op.batch_alter_table("assertion", schema=None) as batch_op:
        batch_op.add_column(sa.Column("review_flags", sa.String(length=40), nullable=True))
    assertion = sa.table(
        "assertion",
        sa.column("predicate", sa.String),
        sa.column("evidence", sa.Text),
        sa.column("review_flags", sa.String),
    )
    evidence = sa.cast(assertion.c.evidence, sa.Text)
    conflict = sa.or_(*[evidence.like(f'%"{f}"%') for f in CONFLICTS])
    ambiguous = evidence.like('%"name_ambiguity"%')
    op.execute(
        assertion.update()
        .where(assertion.c.predicate == "authored")
        .values(
            review_flags=sa.case(
                (sa.and_(conflict, ambiguous), "conflict ambiguous"),
                (conflict, "conflict"),
                (ambiguous, "ambiguous"),
                else_=None,
            )
        )
    )
    with op.batch_alter_table("assertion", schema=None) as batch_op:
        batch_op.create_index("ix_assertion_links", LINKS, unique=False)
        batch_op.create_index("ix_assertion_queue", QUEUE, unique=False)
        batch_op.create_index("ix_assertion_objects", OBJECTS, unique=False)
        batch_op.drop_index(batch_op.f("ix_assertion_subject_id_predicate"))
        batch_op.drop_index(batch_op.f("ix_assertion_predicate_status"))
        batch_op.drop_index(batch_op.f("ix_assertion_object_id_predicate"))


def downgrade() -> None:
    with op.batch_alter_table("assertion", schema=None) as batch_op:
        old = batch_op.f("ix_assertion_subject_id_predicate")
        batch_op.create_index(old, ["subject_id", "predicate"], unique=False)
        old = batch_op.f("ix_assertion_predicate_status")
        batch_op.create_index(old, ["predicate", "status"], unique=False)
        old = batch_op.f("ix_assertion_object_id_predicate")
        batch_op.create_index(old, ["object_id", "predicate"], unique=False)
        batch_op.drop_index("ix_assertion_objects")
        batch_op.drop_index("ix_assertion_queue")
        batch_op.drop_index("ix_assertion_links")
        batch_op.drop_column("review_flags")
