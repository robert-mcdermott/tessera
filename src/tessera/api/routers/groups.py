"""Groups and memberships: read by anyone signed in, curated by admins (slice 4 adds managers).

A group is addressed by its ID, its address (slug), or its code; a merged group's address
answers with the group it was merged into.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.engine import Connection

from tessera.analytics.vocabulary import mesh_year
from tessera.api.access import principal_of
from tessera.api.common import (
    Page,
    assertion_view,
    config_of,
    engine_of,
    envelope,
    index_of,
    lake_of,
    page,
    publication_summaries,
    refresh_index,
)
from tessera.config import VocabularyConfig
from tessera.core.errors import TesseraError
from tessera.evidence import store as people_store
from tessera.groups import inbox, openings, pages, profile, store, themes
from tessera.groups.importer import import_groups, parse_rows
from tessera.ledger.tables import assertion, person, unit

router = APIRouter(prefix="/api/v1", tags=["groups"])


def _vocabulary(request: Request) -> VocabularyConfig:
    return config_of(request).vocabulary


def _fail(exc: TesseraError) -> HTTPException:
    status = 404 if isinstance(exc, store.GroupNotFound) else 400
    return HTTPException(status_code=status, detail=str(exc))


def _resolve(conn: Connection, ref: str) -> dict[str, Any]:
    try:
        return store.resolve(conn, ref)
    except store.GroupNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _view(
    row: dict[str, Any],
    vocabulary: VocabularyConfig,
    *,
    parents: list[dict[str, Any]],
    counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "slug": row["slug"],
        "kind": row["kind"],
        "kind_label": vocabulary.kind_label(row["kind"]),
        "short_name": row["short_name"],
        "code": row["code"],
        "description": row["description"],
        "email": row["email"],
        "website": row["website"],
        "status": row["status"],
        "public": bool(row.get("public")),
        "source": row["source"],
        "external_id": row["external_id"],
        "parents": [{**p, "kind_label": vocabulary.kind_label(p["kind"])} for p in parents],
        "members": (counts or {}).get("current", 0),
        "alumni": (counts or {}).get("alumni", 0),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@router.get("/groups", summary="Groups, with their kinds, parents, and member counts")
def list_groups(
    request: Request,
    p: Annotated[Page, Depends(page)],
    q: Annotated[str | None, Query(max_length=200, description="Words in a name or code")] = None,
    kind: Annotated[list[str] | None, Query(description="Only these kinds")] = None,
    status: Literal["active", "archived", "all"] = "active",
    parent: Annotated[
        str | None, Query(max_length=200, description="Only groups directly under this one")
    ] = None,
) -> dict[str, Any]:
    vocabulary = _vocabulary(request)
    query = select(unit).where(unit.c.status != "merged")
    if status != "all":
        query = query.where(unit.c.status == status)
    if kind:
        query = query.where(unit.c.kind.in_(kind))
    for word in (q or "").split():
        like = f"%{word.lower()}%"
        query = query.where(
            or_(
                func.lower(unit.c.name).like(like),
                func.lower(func.coalesce(unit.c.code, "")).like(like),
                func.lower(func.coalesce(unit.c.short_name, "")).like(like),
            )
        )
    with engine_of(request).connect() as conn:
        if parent:
            children = {c["id"] for c in store.children_of(conn, _resolve(conn, parent)["id"])}
            query = query.where(unit.c.id.in_(sorted(children)))
        rows = [dict(r) for r in conn.execute(query.order_by(unit.c.name)).mappings()]
        counts = store.member_counts(conn)
        order = {k: i for i, (k, _) in enumerate(vocabulary.kinds)}
        rows.sort(key=lambda r: (order.get(r["kind"], len(order)), r["name"].lower()))
        total = len(rows)
        rows = rows[p.offset : p.offset + p.limit]
        parents = store.parents_of(conn, [r["id"] for r in rows])
        leaders = _leaders(conn, [r["id"] for r in rows], vocabulary)
        kinds = dict(
            conn.execute(
                select(unit.c.kind, func.count())
                .where(unit.c.status == "active")
                .group_by(unit.c.kind)
            ).all()
        )
    return envelope(
        [
            {
                **_view(
                    r, vocabulary, parents=parents.get(r["id"], []), counts=counts.get(r["id"])
                ),
                "leaders": leaders.get(r["id"], []),
            }
            for r in rows
        ],
        total=total,
        next_cursor=p.next_cursor(total),
        kinds=[
            {"id": k, "label": label, "count": kinds.get(k, 0)} for k, label in vocabulary.kinds
        ],
        vocabulary=vocabulary.to_json(),
    )


def _leaders(
    conn: Connection, group_ids: list[str], vocabulary: VocabularyConfig
) -> dict[str, list[str]]:
    """The names of each group's current leaders."""
    today = date.today()
    found: dict[str, list[tuple[int, str]]] = {}
    rows = [
        r
        for r in store.memberships(conn, group_ids=group_ids)
        if (r["qualifiers"] or {}).get("role") in vocabulary.leader_roles
        and store.membership_view(r, vocabulary, today)["current"]
    ]
    ids = sorted({r["subject_id"] for r in rows})
    names = dict(
        conn.execute(select(person.c.id, person.c.display_name).where(person.c.id.in_(ids))).all()
    )
    order = {k: i for i, k in enumerate(vocabulary.leader_roles)}
    for r in rows:
        if r["subject_id"] in names:
            found.setdefault(r["object_id"], []).append(
                (order.get(r["qualifiers"]["role"], 99), names[r["subject_id"]])
            )
    return {g: [n for _, n in sorted(v)] for g, v in found.items()}


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    kind: str = Field(max_length=40)
    slug: str | None = Field(
        None, max_length=120, description="The address; made from the name if empty"
    )
    short_name: str | None = Field(None, max_length=80)
    code: str | None = Field(None, max_length=80, description="The organization's own code")
    description: str | None = Field(None, max_length=4000)
    email: str | None = Field(None, max_length=320, description="A contact address")
    website: str | None = Field(None, max_length=500)
    parents: list[str] = Field(
        default_factory=list, max_length=50, description="IDs, addresses, or codes"
    )


class GroupChange(BaseModel):
    name: str | None = Field(None, max_length=300)
    kind: str | None = Field(None, max_length=40)
    slug: str | None = Field(None, max_length=120)
    short_name: str | None = Field(None, max_length=80, description="'' to clear")
    code: str | None = Field(None, max_length=80, description="'' to clear")
    description: str | None = Field(None, max_length=4000, description="'' to clear")
    email: str | None = Field(None, max_length=320, description="'' to clear")
    website: str | None = Field(None, max_length=500, description="'' to clear")
    status: Literal["active", "archived"] | None = None
    parents: list[str] | None = Field(None, max_length=50, description="Replaces the parents")


def _parent_ids(conn: Connection, refs: list[str]) -> list[str]:
    return [_resolve(conn, r)["id"] for r in refs]


def _members_of(conn: Connection, group_ids: list[str]) -> set[str]:
    return {m["subject_id"] for m in store.memberships(conn, group_ids=group_ids)}


@router.post("/groups", summary="Create a group (admins)", status_code=201)
def create(request: Request, body: GroupIn) -> dict[str, Any]:
    actor = principal_of(request).name
    try:
        with engine_of(request).begin() as conn:
            group_id = store.create_group(
                conn,
                body.model_dump(exclude={"parents"}, exclude_none=True),
                actor=actor,
                vocabulary=_vocabulary(request),
                parents=_parent_ids(conn, body.parents),
            )
    except TesseraError as exc:
        raise _fail(exc) from exc
    return detail(request, group_id)


@router.post("/groups/import", summary="Import groups and memberships from a source (admins)")
def import_rows(
    request: Request,
    body: dict[str, Any],
    source: str = Query(max_length=40, description="Where the rows come from, like hr"),
    complete: bool = Query(False, description="The rows are the source's whole list"),
    dry_run: bool = Query(False, description="Report what would change; change nothing"),
) -> dict[str, Any]:
    try:
        stats = import_groups(
            engine_of(request),
            parse_rows(body),
            source=source,
            vocabulary=_vocabulary(request),
            actor=principal_of(request).name,
            complete=complete,
            dry_run=dry_run,
        )
    except TesseraError as exc:
        raise _fail(exc) from exc
    if not dry_run and stats.people:
        refresh_index(request, people=stats.people)
    return envelope(stats.to_json())


@router.get("/groups/{group_ref}", summary="One group: its fields, parents, and sub-groups")
def detail(request: Request, group_ref: str) -> dict[str, Any]:
    vocabulary = _vocabulary(request)
    with engine_of(request).connect() as conn:
        row = _resolve(conn, group_ref)
        parents = store.parents_of(conn, [row["id"]]).get(row["id"], [])
        children = store.children_of(conn, row["id"])
        counts = store.member_counts(conn)
        merged = [
            {"id": r.id, "name": r.name}
            for r in conn.execute(
                select(unit.c.id, unit.c.name).where(unit.c.merged_into == row["id"])
            )
        ]
    view = _view(row, vocabulary, parents=parents, counts=counts.get(row["id"]))
    view["children"] = [
        {
            **c,
            "kind_label": vocabulary.kind_label(c["kind"]),
            "members": counts.get(c["id"], {}).get("current", 0),
        }
        for c in children
    ]
    view["merged_from"] = merged
    return envelope(view, vocabulary=vocabulary.to_json())


@router.post("/groups/{group_ref}", summary="Change, nest, archive, or restore a group (admins)")
def change(request: Request, group_ref: str, body: GroupChange) -> dict[str, Any]:
    actor = principal_of(request).name
    fields = body.model_dump(exclude={"parents"}, exclude_unset=True)
    try:
        with engine_of(request).begin() as conn:
            row = _resolve(conn, group_ref)
            changed = store.update_group(
                conn, row["id"], fields, actor=actor, vocabulary=_vocabulary(request)
            )
            if body.parents is not None:
                store.set_parents(conn, row["id"], _parent_ids(conn, body.parents), actor=actor)
            members = _members_of(conn, [row["id"]]) if {"name", "status"} & set(changed) else set()
    except TesseraError as exc:
        raise _fail(exc) from exc
    if members:  # their profiles and the directory's facets name the group
        refresh_index(request, people=members)
    return detail(request, row["id"])


class MergeIn(BaseModel):
    into: str = Field(max_length=200, description="The group that remains: ID, address, or code")


@router.post("/groups/{group_ref}/merge", summary="Merge a group into another (admins)")
def merge(request: Request, group_ref: str, body: MergeIn) -> dict[str, Any]:
    actor = principal_of(request).name
    try:
        with engine_of(request).begin() as conn:
            source = _resolve(conn, group_ref)
            target = _resolve(conn, body.into)
            members = _members_of(conn, [source["id"], target["id"]])
            moved = store.merge_groups(conn, source["id"], target["id"], actor=actor)
    except TesseraError as exc:
        raise _fail(exc) from exc
    refresh_index(request, people=members)
    return envelope({"merged": source["id"], "into": target["id"], **moved})


def _member_rows(
    conn: Connection, group_id: str, vocabulary: VocabularyConfig, include: str
) -> list[dict[str, Any]]:
    statuses = ("accepted", "rejected") if include == "removed" else ("accepted",)
    rows = store.memberships(conn, group_ids=[group_id], statuses=statuses)
    today = date.today()
    views = [store.membership_view(r, vocabulary, today) for r in rows]
    if include == "current":
        views = [v for v in views if v["current"]]
    elif include == "alumni":
        current = {v["person_id"] for v in views if v["current"]}
        views = [v for v in views if not v["current"] and v["person_id"] not in current]
    elif include == "removed":
        views = [v for v in views if v["status"] == "rejected"]
    ids = sorted({v["person_id"] for v in views})
    who = {
        r.id: r
        for r in conn.execute(
            select(person.c.id, person.c.display_name, person.c.family_name, person.c.status)
            .where(person.c.id.in_(ids))
        )
    }  # fmt: skip
    roles = {k: i for i, (k, _) in enumerate(vocabulary.roles)}
    for v in views:
        found = who.get(v["person_id"])
        v["name"] = found.display_name if found else v["person_id"]
        v["person_status"] = found.status if found else None
        v["_sort"] = ((found.family_name if found else None) or v["name"]).lower()
    views.sort(
        key=lambda v: (
            not v["current"],
            not v["leader"],
            roles.get(v["role"], len(roles)),
            v["_sort"],
        )
    )
    for v in views:
        del v["_sort"]
    return views


@router.get("/groups/{group_ref}/members", summary="A group's members, current or past")
def members(
    request: Request,
    group_ref: str,
    include: Literal["current", "alumni", "all", "removed"] = Query(
        "current", description="current members, alumni, everyone, or removed memberships"
    ),
) -> dict[str, Any]:
    vocabulary = _vocabulary(request)
    with engine_of(request).connect() as conn:
        row = _resolve(conn, group_ref)
        views = _member_rows(conn, row["id"], vocabulary, include)
    return envelope(views, total=len(views), group={"id": row["id"], "name": row["name"]})


class MembershipIn(BaseModel):
    person_id: str = Field(max_length=40)
    role: str | None = Field(None, max_length=40, description="A configured role; member if empty")
    title: str | None = Field(None, max_length=200)
    start: str | None = Field(None, max_length=10, description="2015, 2015-07, or 2015-07-01")
    end: str | None = Field(None, max_length=10, description="Empty while current")


class MembershipChange(BaseModel):
    role: str | None = Field(None, max_length=40)
    title: str | None = Field(None, max_length=200, description="'' to clear")
    start: str | None = Field(None, max_length=10, description="'' to clear")
    end: str | None = Field(None, max_length=10, description="A date ends it; '' makes it current")


class RemovalIn(BaseModel):
    note: str | None = Field(None, max_length=1000)


@router.post(
    "/groups/{group_ref}/members", summary="Add someone to a group (admins)", status_code=201
)
def add_member(request: Request, group_ref: str, body: MembershipIn) -> dict[str, Any]:
    vocabulary = _vocabulary(request)
    actor = principal_of(request).name
    try:
        with engine_of(request).begin() as conn:
            row = _resolve(conn, group_ref)
            person_id = people_store.survivor_of(conn, body.person_id)
            membership_id, action = store.add_membership(
                conn, person_id=person_id, group_id=row["id"], vocabulary=vocabulary,
                actor=actor, role=body.role, title=body.title, start=body.start, end=body.end,
            )  # fmt: skip
            view = store.membership_view(store.get_membership(conn, membership_id), vocabulary)
    except TesseraError as exc:
        raise _fail(exc) from exc
    refresh_index(request, people=[person_id])
    return envelope(view, action=action)


@router.post(
    "/groups/{group_ref}/members/{membership_id}",
    summary="Change a membership's role, title, or dates; an end date ends it (admins)",
)
def change_member(
    request: Request, group_ref: str, membership_id: str, body: MembershipChange
) -> dict[str, Any]:
    vocabulary = _vocabulary(request)
    try:
        with engine_of(request).begin() as conn:
            row = _resolve(conn, group_ref)
            membership = store.get_membership(conn, membership_id)
            if membership["object_id"] != row["id"]:
                raise store.GroupNotFound(f"No membership {membership_id!r} in {row['name']}.")
            store.update_membership(
                conn, membership_id, body.model_dump(exclude_unset=True),
                actor=principal_of(request).name, vocabulary=vocabulary,
            )  # fmt: skip
            view = store.membership_view(store.get_membership(conn, membership_id), vocabulary)
    except TesseraError as exc:
        raise _fail(exc) from exc
    refresh_index(request, people=[membership["subject_id"]])
    return envelope(view)


@router.delete(
    "/groups/{group_ref}/members/{membership_id}",
    summary="Remove a membership that was wrong (admins); to record a departure, end it",
)
def remove_member(
    request: Request, group_ref: str, membership_id: str, body: RemovalIn | None = None
) -> dict[str, Any]:
    try:
        with engine_of(request).begin() as conn:
            row = _resolve(conn, group_ref)
            membership = store.get_membership(conn, membership_id)
            if membership["object_id"] != row["id"]:
                raise store.GroupNotFound(f"No membership {membership_id!r} in {row['name']}.")
            store.remove_membership(
                conn, membership_id, actor=principal_of(request).name,
                note=body.note if body else None,
            )  # fmt: skip
    except TesseraError as exc:
        raise _fail(exc) from exc
    refresh_index(request, people=[membership["subject_id"]])
    return envelope({"id": membership_id, "status": "rejected"})


@router.get("/groups/{group_ref}/history", summary="Every change to a group and its memberships")
def history(
    request: Request, group_ref: str, limit: int = Query(100, ge=1, le=500)
) -> dict[str, Any]:
    with engine_of(request).connect() as conn:
        row = _resolve(conn, group_ref)
        events = store.events(conn, row["id"], limit)
        ids = sorted({e["detail"].get("person_id") for e in events if e["detail"].get("person_id")})
    names = people_store.display_names(engine_of(request), ids) if ids else {}
    return envelope(
        [
            {
                "id": e["id"],
                "action": e["action"],
                "actor": e["actor"],
                "at": e["at"],
                "detail": e["detail"],
                "person": names.get(e["detail"].get("person_id")),
            }
            for e in events
        ]
    )


# --- the showcase (LAB-01, LAB-02, LAB-03) ------------------------------------------------------


def _can_edit(request: Request, group_id: str) -> bool:
    return principal_of(request).edits_group(engine_of(request), group_id)


def _require_edit(request: Request, group_id: str) -> None:
    if not _can_edit(request, group_id):
        raise HTTPException(
            status_code=403, detail="You may decide only for the groups in your scope."
        )


@router.get(
    "/groups/{group_ref}/overview",
    summary="A group's showcase: leaders, people, topics, grants, studies, and connections",
)
def overview(request: Request, group_ref: str) -> dict[str, Any]:
    config = config_of(request)
    home = request.app.state.home
    today = date.today()
    with engine_of(request).connect() as conn:
        row = _resolve(conn, group_ref)
        current, alumni = profile.people_of(conn, row["id"], config, today)
        ids = sorted({v["person_id"] for v in current})
        grants = profile.linked(conn, ids, "investigator_on")
        studies = profile.linked(conn, ids, "official_of")
        accepted = profile.attributions(conn, row["id"])
        suggested = [
            a for a in profile.attributions(conn, row["id"], ("suggested",)) if a["reported"]
        ]
        works_with = profile.connections(conn, row["id"], ids, config, today)
        parents = store.parents_of(conn, [row["id"]]).get(row["id"], [])
        children = store.children_of(conn, row["id"])
        counts = store.member_counts(conn, today)
        open_now = openings.list_openings(conn, [row["id"]], today)
        theme_rows = themes.list_themes(conn, row["id"])
    topics, areas = profile.topics_and_areas(home, ids)
    index = index_of(request)
    try:
        summaries = index.summaries([*grants, *studies])
    except Exception:  # no index yet
        summaries = {}
    names = {v["person_id"]: v["name"] for v in current}

    def records(found: dict[str, list[str]]) -> list[dict[str, Any]]:
        return [
            {
                "ref": ref,
                "summary": summaries.get(ref, {"id": ref}),
                "people": [{"id": p, "name": names.get(p, p)} for p in sorted(set(people))],
            }
            for ref, people in found.items()
        ]

    grant_items = records(grants)
    grant_items.sort(key=lambda g: -((g["summary"].get("fiscal_years") or [0, 0])[1] or 0))
    study_items = records(studies)
    pubs = publication_summaries(
        index, lake_of(request), [a["subject_id"].removeprefix("pub:") for a in accepted]
    )
    by_year = Counter(s.get("year") for s in pubs.values() if s.get("year"))
    view = _view(row, config.vocabulary, parents=parents, counts=counts.get(row["id"]))
    view["children"] = [
        {**c, "kind_label": config.vocabulary.kind_label(c["kind"]),
         "members": counts.get(c["id"], {}).get("current", 0)}
        for c in children if c["status"] == "active"
    ]  # fmt: skip
    return envelope(
        {
            **view,
            "leaders": [v for v in current if v["leader"]],
            "people": current,
            "alumni": alumni,
            "topics": topics,
            "areas": areas,
            "grants": grant_items,
            "studies": study_items,
            "publications": {
                "accepted": len(accepted),
                "suggested": len(suggested),
                "by_year": [{"year": y, "count": by_year[y]} for y in sorted(by_year)],
            },
            "connections": works_with,
            "openings": open_now,
            "themes": themes_view(request, theme_rows),
            "can_edit": _can_edit(request, row["id"]),
        },
        vocabulary=config.vocabulary.to_json(),
        mesh_year=mesh_year(home),
    )


@router.get(
    "/groups/{group_ref}/publications",
    summary="Publications attributed to a group, or suggested for it",
)
def group_publications(
    request: Request,
    group_ref: str,
    p: Annotated[Page, Depends(page)],
    status: Literal["accepted", "suggested", "rejected"] = "accepted",
) -> dict[str, Any]:
    config = config_of(request)
    with engine_of(request).connect() as conn:
        row = _resolve(conn, group_ref)
        rows = profile.attributions(conn, row["id"], (status,))
        if status == "suggested":
            rows = [r for r in rows if r["reported"]]
        members = sorted({m for r in rows for m in (r["qualifiers"] or {}).get("members", [])})
        names = dict(
            conn.execute(
                select(person.c.id, person.c.display_name).where(person.c.id.in_(members))
            ).all()
        )
    pmids = [r["subject_id"].removeprefix("pub:") for r in rows]
    summaries = publication_summaries(index_of(request), lake_of(request), pmids)
    items = [
        {
            "assertion": assertion_view(r, config),
            "summary": summaries.get(r["subject_id"].removeprefix("pub:"), {}),
            "members": [
                {"id": m, "name": names.get(m, m)}
                for m in (r["qualifiers"] or {}).get("members", [])
            ],
        }
        for r in rows
    ]
    if status == "suggested":
        items.sort(
            key=lambda i: (-(i["assertion"]["score"] or 0), -(i["summary"].get("year") or 0))
        )
    else:
        items.sort(
            key=lambda i: (-(i["summary"].get("year") or 0), i["summary"].get("title") or "")
        )
    total = len(items)
    return envelope(
        items[p.offset : p.offset + p.limit],
        total=total,
        next_cursor=p.next_cursor(total),
        can_edit=_can_edit(request, row["id"]),
    )


class AttributionDecision(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500, description="attributed_to link IDs")
    action: Literal["accept", "reject", "defer", "undo"]
    note: str | None = Field(None, max_length=1000)


@router.post(
    "/groups/{group_ref}/publications/decisions",
    summary="Decide whether papers are the group's own (its managers, curators, and admins)",
)
def decide_publications(
    request: Request, group_ref: str, body: AttributionDecision
) -> dict[str, Any]:
    engine = engine_of(request)
    with engine.connect() as conn:
        row = _resolve(conn, group_ref)
        found = {
            r.id: r.subject_id
            for r in conn.execute(
                select(assertion.c.id, assertion.c.subject_id).where(
                    assertion.c.id.in_(body.ids),
                    assertion.c.predicate == "attributed_to",
                    assertion.c.object_id == row["id"],
                )
            )
        }
    _require_edit(request, row["id"])
    missing = [i for i in body.ids if i not in found]
    if missing:
        raise HTTPException(status_code=404, detail=f"No suggestion {missing[0]!r} for this group.")
    actor = principal_of(request).name
    results = []
    for assertion_id in body.ids:
        try:
            results.append(people_store.decide(engine, assertion_id, body.action, actor, body.note))
        except TesseraError as exc:
            raise _fail(exc) from exc
    return envelope(results, total=len(results))


# --- the group's page: blocks, draft, publish, history, rollback (LAB-04, LAB-05) -----------


def _featured(request: Request, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    pmids = [p for b in blocks if b["type"] == "publications" for p in b.get("featured", [])]
    if not pmids:
        return {}
    return publication_summaries(index_of(request), lake_of(request), pmids)


def _page_view(request: Request, row: dict[str, Any], blocks: list[dict], **extra: Any) -> dict:
    return {
        "group_id": row["id"],
        "blocks": blocks,
        "featured": _featured(request, blocks),
        "block_types": [
            {"id": k, "label": v, "repeatable": k in pages.REPEATABLE, "side": k in pages.SIDE}
            for k, v in pages.BLOCK_TYPES.items()
        ],
        **extra,
    }


@router.get("/groups/{group_ref}/page", summary="A group's page: its published blocks, or a draft")
def page_blocks(
    request: Request,
    group_ref: str,
    version: Annotated[
        str, Query(max_length=10, description="published, draft (editors), or a revision number")
    ] = "published",
) -> dict[str, Any]:
    with engine_of(request).connect() as conn:
        row = _resolve(conn, group_ref)
        page_row = pages.get_page(conn, row["id"]) or {}
        can_edit = _can_edit(request, row["id"])
        status = {
            "revision": page_row.get("revision") or 0,
            "published_at": page_row.get("published_at"),
            "published_by": page_row.get("published_by"),
            "has_draft": page_row.get("draft") is not None,
            "draft_updated_at": page_row.get("draft_updated_at") if can_edit else None,
            "draft_updated_by": page_row.get("draft_updated_by") if can_edit else None,
            "can_edit": can_edit,
        }
        if version == "draft":
            if not can_edit:
                raise HTTPException(
                    status_code=403, detail="Only the group's editors see its draft."
                )
            blocks = page_row.get("draft")
            if blocks is None:
                blocks, _ = pages.published_blocks(conn, row["id"])
            shown = "draft"
            is_default = False
        elif version.isdigit():
            blocks = pages.revision_blocks(conn, row["id"], int(version))
            if blocks is None:
                raise HTTPException(status_code=404, detail=f"This page has no version {version}.")
            shown, is_default = int(version), False
        else:
            blocks, is_default = pages.published_blocks(conn, row["id"])
            shown = "published"
    return envelope(_page_view(request, row, blocks, version=shown, default=is_default, **status))


class PageDraft(BaseModel):
    blocks: list[dict[str, Any]] = Field(max_length=pages.MAX_BLOCKS)


class PageNote(BaseModel):
    note: str | None = Field(None, max_length=500)


class PageRollback(BaseModel):
    revision: int = Field(ge=1)
    note: str | None = Field(None, max_length=500)


def _page_change(request: Request, group_ref: str, change) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    actor = principal_of(request).name
    try:
        with engine_of(request).begin() as conn:
            row = _resolve(conn, group_ref)
            _require_edit(request, row["id"])
            change(conn, row["id"], actor)
    except TesseraError as exc:
        raise _fail(exc) from exc
    return page_blocks(request, row["id"], version="draft")


@router.post("/groups/{group_ref}/page/draft", summary="Save the group page's draft (its editors)")
def save_page_draft(request: Request, group_ref: str, body: PageDraft) -> dict[str, Any]:
    return _page_change(
        request,
        group_ref,
        lambda conn, gid, actor: pages.save_draft(conn, gid, body.blocks, actor=actor),
    )


@router.delete("/groups/{group_ref}/page/draft", summary="Discard the group page's draft")
def discard_page_draft(request: Request, group_ref: str) -> dict[str, Any]:
    return _page_change(
        request, group_ref, lambda conn, gid, actor: pages.discard_draft(conn, gid, actor=actor)
    )


@router.post("/groups/{group_ref}/page/publish", summary="Publish the draft as the group's page")
def publish_page(request: Request, group_ref: str, body: PageNote) -> dict[str, Any]:
    return _page_change(
        request,
        group_ref,
        lambda conn, gid, actor: pages.publish(conn, gid, actor=actor, note=body.note),
    )


@router.post("/groups/{group_ref}/page/rollback", summary="Publish an earlier version again")
def rollback_page(request: Request, group_ref: str, body: PageRollback) -> dict[str, Any]:
    return _page_change(
        request,
        group_ref,
        lambda conn, gid, actor: pages.rollback(
            conn, gid, body.revision, actor=actor, note=body.note
        ),
    )


@router.get("/groups/{group_ref}/page/revisions", summary="Every published version of the page")
def page_revisions(request: Request, group_ref: str) -> dict[str, Any]:
    with engine_of(request).connect() as conn:
        row = _resolve(conn, group_ref)
        found = pages.revisions(conn, row["id"])
    return envelope(
        [
            {
                "number": r["number"],
                "action": r["action"],
                "actor": r["actor"],
                "note": r["note"],
                "at": r["at"],
                "blocks": len(r["blocks"]),
            }
            for r in found
        ]
    )


# --- openings, themes, the inbox, and digests (LAB-06, LAB-07, LAB-08, CUR-08) ----------------


def _expand_links(request: Request, refs: list[str]) -> dict[str, dict[str, Any]]:
    """Labels for theme links: people's names and records' titles."""
    people = [r for r in refs if r.startswith("per_")]
    records = [r for r in refs if not r.startswith("per_")]
    out: dict[str, dict[str, Any]] = {}
    if people:
        names = people_store.display_names(engine_of(request), people)
        out |= {p: {"ref": p, "kind": "person", "label": names.get(p, p)} for p in people}
    if records:
        try:
            summaries = index_of(request).summaries(records)
        except Exception:
            summaries = {}
        kinds = {"grt": "grant", "pub": "publication", "stu": "study"}
        for r in records:
            s = summaries.get(r) or {}
            out[r] = {"ref": r, "kind": kinds[r[:3]], "label": s.get("title") or r.split(":", 1)[1]}
    return out


def themes_view(request: Request, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    labels = _expand_links(request, sorted({r for t in rows for r in t["links"]}))
    return [{**t, "links": [labels[r] for r in t["links"] if r in labels]} for t in rows]


@router.get("/groups/{group_ref}/openings", summary="A group's openings (open ones, or all)")
def group_openings(
    request: Request, group_ref: str, include: Literal["open", "all"] = "open"
) -> dict[str, Any]:
    with engine_of(request).connect() as conn:
        row = _resolve(conn, group_ref)
        if include == "all":
            _require_edit(request, row["id"])
        items = openings.list_openings(
            conn, [row["id"]], date.today(), include_closed=include == "all"
        )
    return envelope(items, total=len(items), kinds=openings.KINDS)


class OpeningIn(BaseModel):
    title: str | None = Field(None, max_length=200)
    kind: Literal["job", "training", "other"] | None = None
    description: str | None = Field(None, max_length=4000)
    url: str | None = Field(None, max_length=500, description="Where to apply")
    contact: str | None = Field(None, max_length=320)
    posted_on: date | None = None
    closes_on: date | None = Field(None, description="It closes by itself after this day")
    closed: bool | None = Field(None, description="true closes it now; false opens it again")


def _opening_change(request: Request, group_ref: str, run) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    try:
        with engine_of(request).begin() as conn:
            row = _resolve(conn, group_ref)
            _require_edit(request, row["id"])
            opening_id = run(conn, row["id"], principal_of(request).name)
            view = openings.view(openings.get_opening(conn, opening_id), date.today())
    except TesseraError as exc:
        raise _fail(exc) from exc
    return envelope(view)


@router.post("/groups/{group_ref}/openings", summary="Post an opening", status_code=201)
def add_opening(request: Request, group_ref: str, body: OpeningIn) -> dict[str, Any]:
    fields = body.model_dump(exclude={"closed"}, exclude_none=True)
    return _opening_change(
        request,
        group_ref,
        lambda conn, gid, actor: openings.create_opening(
            conn, gid, fields, actor=actor, today=date.today()
        ),
    )


@router.post("/groups/{group_ref}/openings/{opening_id}", summary="Change, close, or reopen")
def change_opening(
    request: Request, group_ref: str, opening_id: str, body: OpeningIn
) -> dict[str, Any]:
    fields = body.model_dump(exclude={"closed"}, exclude_unset=True)

    def run(conn: Connection, gid: str, actor: str) -> str:
        if openings.get_opening(conn, opening_id)["unit_id"] != gid:
            raise store.GroupNotFound(f"No opening {opening_id!r} in this group.")
        openings.update_opening(conn, opening_id, fields, actor=actor, close=body.closed)
        return opening_id

    return _opening_change(request, group_ref, run)


@router.get("/groups/{group_ref}/themes", summary="A group's research themes and projects")
def group_themes(
    request: Request, group_ref: str, include: Literal["active", "all"] = "active"
) -> dict[str, Any]:
    with engine_of(request).connect() as conn:
        row = _resolve(conn, group_ref)
        rows = themes.list_themes(conn, row["id"], include_archived=include == "all")
    return envelope(themes_view(request, rows), total=len(rows), kinds=themes.KINDS)


class ThemeIn(BaseModel):
    name: str | None = Field(None, max_length=200)
    kind: Literal["theme", "project"] | None = None
    summary: str | None = Field(None, max_length=4000)
    status: Literal["active", "archived"] | None = None
    links: list[str] | None = Field(
        None, max_length=themes.MAX_LINKS, description="per_…, grt:…, pub:…, or stu:… references"
    )


def _theme_change(request: Request, group_ref: str, run) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    try:
        with engine_of(request).begin() as conn:
            row = _resolve(conn, group_ref)
            _require_edit(request, row["id"])
            theme_id = run(conn, row["id"], principal_of(request).name)
            found = [t for t in themes.list_themes(conn, row["id"], include_archived=True)
                     if t["id"] == theme_id]  # fmt: skip
    except TesseraError as exc:
        raise _fail(exc) from exc
    return envelope(themes_view(request, found)[0])


@router.post("/groups/{group_ref}/themes", summary="Add a theme or project", status_code=201)
def add_theme(request: Request, group_ref: str, body: ThemeIn) -> dict[str, Any]:
    fields = body.model_dump(exclude={"links"}, exclude_none=True)
    return _theme_change(
        request,
        group_ref,
        lambda conn, gid, actor: themes.create_theme(
            conn, gid, fields, actor=actor, links=body.links or []
        ),
    )


@router.post("/groups/{group_ref}/themes/{theme_id}", summary="Change a theme or its links")
def change_theme(request: Request, group_ref: str, theme_id: str, body: ThemeIn) -> dict[str, Any]:
    fields = body.model_dump(exclude={"links"}, exclude_unset=True)

    def run(conn: Connection, gid: str, actor: str) -> str:
        if themes.get_theme(conn, theme_id)["unit_id"] != gid:
            raise store.GroupNotFound(f"No theme {theme_id!r} in this group.")
        themes.update_theme(conn, theme_id, fields, actor=actor, links=body.links)
        return theme_id

    return _theme_change(request, group_ref, run)


@router.get("/groups/{group_ref}/inbox", summary="The group's weekly inbox (its editors)")
def group_inbox(request: Request, group_ref: str) -> dict[str, Any]:
    config = config_of(request)
    with engine_of(request).connect() as conn:
        row = _resolve(conn, group_ref)
        _require_edit(request, row["id"])
        items = inbox.group_inbox_items(conn, row["id"], config, date.today(),
                                        home=request.app.state.home)  # fmt: skip
    pmids = [a["subject_id"].removeprefix("pub:") for a in items["papers"]]
    summaries = publication_summaries(index_of(request), lake_of(request), pmids)
    try:
        grant_summaries = index_of(request).summaries([g["ref"] for g in items["grants"]])
    except Exception:
        grant_summaries = {}
    return envelope(
        {
            **items,
            "group": {"id": row["id"], "name": row["name"], "slug": row["slug"]},
            "papers": [
                {
                    "assertion": assertion_view(a, config),
                    "summary": summaries.get(a["subject_id"].removeprefix("pub:"), {}),
                }
                for a in sorted(items["papers"], key=lambda a: -(a["score"] or 0))
            ],
            "grants": [
                {**g, "summary": grant_summaries.get(g["ref"], {"id": g["ref"]})}
                for g in items["grants"]
            ],
        }
    )


@router.post("/groups/{group_ref}/inbox/reviewed", summary="Mark the group's inbox as reviewed")
def inbox_reviewed(request: Request, group_ref: str) -> dict[str, Any]:
    try:
        with engine_of(request).begin() as conn:
            row = _resolve(conn, group_ref)
            _require_edit(request, row["id"])
            at = inbox.mark_reviewed(conn, row["id"], actor=principal_of(request).name)
    except TesseraError as exc:
        raise _fail(exc) from exc
    return envelope({"group_id": row["id"], "reviewed_at": at})


@router.get("/inbox", summary="Your digest: pending work in your groups and review queues")
def my_digest(request: Request) -> dict[str, Any]:
    found = inbox.digest(engine_of(request), principal_of(request), config_of(request),
                         home=request.app.state.home)  # fmt: skip
    return envelope(found)


class GroupPublicIn(BaseModel):
    public: bool = Field(description="Whether the group has a page on the public site")


@router.post("/groups/{group_ref}/public", summary="Give the group a public page, or take it away")
def set_group_public(request: Request, group_ref: str, body: GroupPublicIn) -> dict[str, Any]:
    from sqlalchemy import update

    actor = principal_of(request).name
    with engine_of(request).begin() as conn:
        row = _resolve(conn, group_ref)
        _require_edit(request, row["id"])
        if bool(row["public"]) != body.public:
            conn.execute(update(unit).where(unit.c.id == row["id"]).values(public=body.public))
            store._event(conn, row["id"], actor, "public_on" if body.public else "public_off")
    return envelope({"id": row["id"], "public": body.public})
