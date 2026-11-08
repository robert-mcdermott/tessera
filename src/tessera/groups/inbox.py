"""The weekly inbox (LAB-06) and digests of pending work (CUR-08).

A group's inbox gathers, in one place, what its editors need to look at:

* **Papers to attribute:** suggestions waiting for a decision (accept or reject here).
* **New grants:** grants its current members gained since the inbox was last reviewed.
* **Roster changes:** people who joined, left, or changed role since then (from imports, the
  roster, or by hand).
* **Openings closing soon:** open openings that close within two weeks (extend or close them).

**Mark as reviewed** sets the point from which new grants and roster changes count; papers
and openings stay until they are decided. A group never reviewed counts the last week.

A **digest** is the same counts across every group someone edits, with the curation work they
have (the review queue, discovered people, possible duplicates), shown in the app and, when a
mail server is configured, sent by ``tessera digest send``.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection, Engine

from tessera.config import Config
from tessera.core.clock import utc_now
from tessera.groups import openings, store
from tessera.groups.profile import attributions
from tessera.ledger.tables import assertion, group_inbox, person, unit, unit_event

WINDOW = timedelta(days=7)  # a group never reviewed counts the last week
EXPIRING_DAYS = 14
ROSTER_ACTIONS = ("member_add", "member_end", "member_edit", "member_remove")


def reviewed_at(conn: Connection, group_id: str) -> dict[str, Any] | None:
    row = conn.execute(select(group_inbox).where(group_inbox.c.unit_id == group_id)).mappings()
    found = row.first()
    return dict(found) if found else None


def _since(conn: Connection, group_id: str, now: datetime) -> datetime:
    row = reviewed_at(conn, group_id)
    return row["reviewed_at"] if row else now - WINDOW


def group_inbox_items(
    conn: Connection, group_id: str, config: Config, today: date, now: datetime | None = None
) -> dict[str, Any]:
    """Everything in one group's inbox."""
    now = now or utc_now()
    since = _since(conn, group_id, now)
    papers = [a for a in attributions(conn, group_id, ("suggested",)) if a["reported"]]
    current = store.current_members(conn, [group_id], today)
    grants: dict[str, list[str]] = defaultdict(list)
    if current:
        for subject, obj in conn.execute(
            select(assertion.c.subject_id, assertion.c.object_id).where(
                assertion.c.predicate == "investigator_on",
                assertion.c.status.in_(("accepted", "suggested")),
                assertion.c.subject_id.in_(sorted(current)),
                assertion.c.first_seen_at > since,
            )
        ):
            grants[obj].append(subject)
    roster = [
        dict(r)
        for r in conn.execute(
            select(unit_event)
            .where(
                unit_event.c.unit_id == group_id,
                unit_event.c.action.in_(ROSTER_ACTIONS),
                unit_event.c.at > since,
            )
            .order_by(unit_event.c.id.desc())
        ).mappings()
    ]
    ids = sorted(
        {p for people in grants.values() for p in people}
        | {e["detail"].get("person_id") for e in roster if e["detail"].get("person_id")}
    )
    names = dict(
        conn.execute(select(person.c.id, person.c.display_name).where(person.c.id.in_(ids))).all()
    )
    expiring = openings.expiring(conn, [group_id], today, EXPIRING_DAYS)
    return {
        "since": since,
        "reviewed": reviewed_at(conn, group_id),
        "papers": papers,
        "grants": [
            {"ref": ref, "people": [{"id": p, "name": names.get(p, p)} for p in sorted(set(ps))]}
            for ref, ps in sorted(grants.items())
        ],
        "roster": [
            {
                "action": e["action"],
                "person_id": e["detail"].get("person_id"),
                "person": names.get(e["detail"].get("person_id")),
                "role": (e["detail"].get("after") or e["detail"].get("before") or {}).get("role"),
                "end": (e["detail"].get("after") or {}).get("end"),
                "actor": e["actor"],
                "at": e["at"],
            }
            for e in roster
        ],
        "openings": expiring,
        "counts": {
            "papers": len(papers),
            "grants": len(grants),
            "roster": len(roster),
            "openings": len(expiring),
        },
    }


def mark_reviewed(conn: Connection, group_id: str, *, actor: str) -> datetime:
    store.get_group(conn, group_id)
    now = utc_now()
    if reviewed_at(conn, group_id):
        conn.execute(
            update(group_inbox)
            .where(group_inbox.c.unit_id == group_id)
            .values(reviewed_at=now, reviewed_by=actor)
        )
    else:
        conn.execute(
            insert(group_inbox).values(unit_id=group_id, reviewed_at=now, reviewed_by=actor)
        )
    return now


def digest(
    engine: Engine, principal: Any, config: Config, today: date | None = None
) -> dict[str, Any]:
    """Pending work for one person: their groups' inboxes, and their curation queues."""
    from tessera.evidence import store as evidence

    today = today or date.today()
    now = datetime.now(UTC)
    scope = principal.edited_groups(engine)
    groups: list[dict[str, Any]] = []
    with engine.connect() as conn:
        query = select(unit.c.id, unit.c.name, unit.c.slug, unit.c.kind).where(
            unit.c.status == "active"
        )
        if scope is not None:
            query = query.where(unit.c.id.in_(sorted(scope)))
        elif not principal.manages_any:
            # A curator for everyone sees the groups that have something waiting, not all.
            query = query.where(unit.c.kind.in_(config.vocabulary.attributable_kinds))
        for gid, name, slug, kind in conn.execute(query.order_by(unit.c.name)).all():
            counts = group_inbox_items(conn, gid, config, today, now)["counts"]
            if scope is not None or any(counts.values()):
                groups.append(
                    {
                        "id": gid,
                        "name": name,
                        "slug": slug,
                        "kind": kind,
                        "kind_label": config.vocabulary.kind_label(kind),
                        "counts": counts,
                        "total": sum(counts.values()),
                    }
                )
    review: dict[str, int] = {}
    if principal.reviews:
        people = principal.curated_people(engine)
        strong = config.evidence.strong_threshold
        threshold = config.evidence.review_threshold
        rows = [
            r
            for r in evidence.assertions_for(engine, predicate="authored", statuses=("suggested",))
            if (people is None or r["subject_id"] in people) and (r["score"] or 0) >= threshold
        ]
        review["strong"] = sum(1 for r in rows if (r["score"] or 0) >= strong)
        review["possible"] = len(rows) - review["strong"]
    if principal.curates_all:
        with engine.connect() as conn:
            review["candidates"] = len(
                conn.execute(select(person.c.id).where(person.c.status == "candidate")).all()
            )
            review["duplicates"] = len(
                conn.execute(
                    select(assertion.c.id).where(
                        assertion.c.predicate == "same_as",
                        assertion.c.status == "suggested",
                        assertion.c.reported.is_(True),
                    )
                ).all()
            )
    groups.sort(key=lambda g: (-g["total"], g["name"]))
    return {
        "groups": groups,
        "review": review,
        "total": sum(g["total"] for g in groups) + sum(review.values()),
    }


__all__ = ["digest", "group_inbox_items", "mark_reviewed", "reviewed_at"]


def digest_text(result: dict[str, Any], config: Config, name: str) -> tuple[str, str]:
    """(subject, plain text) for one person's digest."""
    base = config.mail.base_url.rstrip("/")
    product = config.branding.product_name
    lines = [f"Hello {name},", "", f"Here is what is waiting for you in {product}.", ""]
    labels = {
        "papers": ("paper to attribute", "papers to attribute"),
        "grants": ("new grant", "new grants"),
        "roster": ("roster change", "roster changes"),
        "openings": ("opening closing soon", "openings closing soon"),
    }
    for g in result["groups"]:
        parts = [
            f"{n} {labels[k][0] if n == 1 else labels[k][1]}" for k, n in g["counts"].items() if n
        ]
        if not parts:
            continue
        lines.append(f"{g['name']} ({g['kind_label']}): {', '.join(parts)}")
        if base:
            lines.append(f"  {base}/inbox?group={g['slug']}")
    review = result["review"]
    if review.get("strong") or review.get("possible"):
        lines.append(
            f"Review queue: {review.get('strong', 0)} strong and {review.get('possible', 0)} "
            "possible suggestions"
        )
        if base:
            lines.append(f"  {base}/review")
    if review.get("candidates"):
        lines.append(f"Discovered people: {review['candidates']}")
    if review.get("duplicates"):
        lines.append(f"Possible duplicates: {review['duplicates']}")
    lines += ["", f"This digest comes from {product}. Your inbox is in the app at any time."]
    total = result["total"]
    subject = f"{product}: {total} {'item' if total == 1 else 'items'} waiting for you"
    return subject, "\n".join(lines) + "\n"
