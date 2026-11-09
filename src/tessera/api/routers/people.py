"""People: the directory and profiles."""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Annotated, Any

import pyarrow as pa
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from tessera.api.access import principal_of, require_curate
from tessera.api.common import (
    Page,
    assertion_view,
    band,
    config_of,
    engine_of,
    envelope,
    index_of,
    lake_of,
    page,
    pick_fields,
    publication_summaries,
    refresh_index,
    split_values,
)
from tessera.core.clock import utc_now
from tessera.core.errors import TesseraError
from tessera.core.ids import new_id
from tessera.evidence import store
from tessera.evidence.merge import active_merges
from tessera.groups import store as groups
from tessera.ledger.tables import (
    assertion,
    curation_event,
    person,
    person_identifier,
    person_name,
    unit,
)

router = APIRouter(prefix="/api/v1/people", tags=["people"])

PUBLICATION_STATUSES = ("suggested", "accepted", "deferred", "rejected")
BANDS = ("strong", "possible", "unlikely")


@router.get("", summary="Directory of people, with search, facets, and sorting")
def list_people(
    request: Request,
    p: Annotated[Page, Depends(page)],
    q: str | None = Query(None, description="Free-text search"),
    status: Annotated[list[str] | None, Query(description="active, former")] = None,
    unit: Annotated[list[str] | None, Query(description="Unit names")] = None,
    sort: str | None = Query(None, description="name | activity | relevance"),
    fields: str | None = Query(None, description="Comma-separated item keys to return"),
) -> dict[str, Any]:
    filters = {"status": split_values(status), "unit": split_values(unit)}
    result = index_of(request).search("person", q, filters, sort, p.limit, p.offset)
    items = [{**item["summary"], "highlights": item["highlights"]} for item in result.items]
    return envelope(
        pick_fields(items, fields),
        total=result.total,
        limit=p.limit,
        next_cursor=p.next_cursor(result.total),
        facets=result.facets,
    )


def _person_row(request: Request, person_id: str) -> dict[str, Any]:
    with engine_of(request).connect() as conn:
        row = conn.execute(select(person).where(person.c.id == person_id)).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="No such person.")
    return dict(row)


def person_identifiers(
    request: Request, person_id: str, authored: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """A person's identifiers, with what a curator needs to judge an ORCID iD.

    For each iD: how many of the person's papers (confirmed or suggested) carry it at their
    author position, what the public ORCID record says, and anyone else who holds it.
    """
    engine = engine_of(request)
    with engine.connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                select(
                    person_identifier.c.scheme,
                    person_identifier.c.value,
                    person_identifier.c.status,
                    person_identifier.c.source,
                )
                .where(person_identifier.c.person_id == person_id)
                .order_by(person_identifier.c.scheme, person_identifier.c.value)
            ).mappings()
        ]
        values = [(r["scheme"], r["value"]) for r in rows]
        others = conn.execute(
            select(
                person_identifier.c.scheme,
                person_identifier.c.value,
                person_identifier.c.status,
                person.c.id,
                person.c.display_name,
            )
            .join(person, person.c.id == person_identifier.c.person_id)
            .where(
                person_identifier.c.person_id != person_id,
                person_identifier.c.status != "rejected",
                person_identifier.c.value.in_([v for _, v in values] or [""]),
            )
        ).mappings()
        also: dict[tuple[str, str], list[dict[str, str]]] = {}
        for o in others:
            if (o["scheme"], o["value"]) in values:
                also.setdefault((o["scheme"], o["value"]), []).append(
                    {"id": o["id"], "name": o["display_name"], "status": o["status"]}
                )
    orcids = [r["value"] for r in rows if r["scheme"] == "orcid"]
    on_papers: Counter = Counter()
    records: dict[str, dict[str, Any]] = {}
    if orcids:
        if authored is None:
            authored = [
                a
                for a in store.assertions_for(engine, subject_id=person_id)
                if a["predicate"] == "authored"
            ]
        slots = [
            (a["object_id"].removeprefix("pub:"), int(a["qualifiers"]["author_position"]))
            for a in authored
            if a["status"] in ("accepted", "suggested")
            and (a["qualifiers"] or {}).get("author_position") is not None
        ]
        lake = lake_of(request)
        cur = lake.cursor()
        try:
            cur.register(
                "_slots",
                pa.table(
                    {
                        "pmid": pa.array([p for p, _ in slots], pa.string()),
                        "position": pa.array([n for _, n in slots], pa.int16()),
                    }
                ),
            )
            cur.register("_orcids", pa.table({"orcid": pa.array(orcids, pa.string())}))
            for orcid, count in cur.execute(
                "SELECT a.orcid, count(*) FROM pubmed_author a JOIN _slots USING (pmid, position) "
                "JOIN _orcids USING (orcid) GROUP BY a.orcid"
            ).fetchall():
                on_papers[orcid] = count
            for orcid, status, given, family, works in cur.execute(
                "SELECT r.orcid, r.status, r.given_names, r.family_name, "
                "(SELECT count(*) FROM orcid_work w WHERE w.orcid = r.orcid) "
                "FROM orcid_record r JOIN _orcids USING (orcid)"
            ).fetchall():
                name = " ".join(n for n in (given, family) if n) or None
                records[orcid] = {"status": status, "name": name, "works": works}
        finally:
            cur.close()
    out = []
    for r in rows:
        item = {**r, "also": also.get((r["scheme"], r["value"]), [])}
        if r["scheme"] == "orcid":
            item |= {
                "url": f"https://orcid.org/{r['value']}",
                "papers": on_papers[r["value"]],
                "record": records.get(r["value"]),
            }
        out.append(item)
    return out


class IdentifierDecision(BaseModel):
    scheme: str = Field("orcid", description="orcid, email, nih_profile, or local")
    value: str = Field(min_length=1, max_length=320)
    action: str = Field(description="add, accept, reject, or remove")
    note: str | None = Field(None, max_length=2000)


@router.post(
    "/{person_id}/identifiers",
    summary="Add, confirm, reject, or remove one of a person's identifiers (logged)",
)
def decide_identifier(request: Request, person_id: str, body: IdentifierDecision) -> dict[str, Any]:
    """Confirming an ORCID iD makes it decisive evidence from the next resolve or sync, and
    its public record is read at the next sync. A rejected iD is kept so that it is never
    proposed again and counts against papers that carry it."""
    _person_row(request, person_id)
    actor = require_curate(request, person_id).name
    try:
        result = store.decide_identifier(
            engine_of(request),
            person_id,
            body.scheme,
            body.value,
            body.action,
            actor,
            body.note,
        )
    except store.InvalidIdentifier as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TesseraError as exc:  # e.g. confirming an iD the person does not have
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return envelope({**result, "identifiers": person_identifiers(request, person_id)})


@router.get("/{person_id}", summary="A person's profile, live from the ledger")
def get_person(request: Request, person_id: str) -> dict[str, Any]:
    config = config_of(request)
    engine = engine_of(request)
    row = _person_row(request, person_id)
    with engine.connect() as conn:
        names = [
            dict(r)
            for r in conn.execute(
                select(
                    person_name.c.family,
                    person_name.c.given,
                    person_name.c.kind,
                    person_name.c.source,
                ).where(person_name.c.person_id == person_id)
            ).mappings()
        ]
        units = {
            r.id: dict(r._mapping)
            for r in conn.execute(
                select(unit.c.id, unit.c.name, unit.c.slug, unit.c.kind, unit.c.status)
            )
        }
    links = store.assertions_for(engine, subject_id=person_id)
    authored = [a for a in links if a["predicate"] == "authored"]
    identifiers = person_identifiers(request, person_id, authored)
    counts: dict[str, Any] = {
        "publications": dict(Counter(a["status"] for a in authored)),
        "suggested_by_band": dict(
            Counter(band(a["score"], config) for a in authored if a["status"] == "suggested")
        ),
        "grants": sum(1 for a in links if a["predicate"] == "investigator_on"),
        "studies": sum(
            1 for a in links if a["predicate"] == "official_of" and a["status"] != "rejected"
        ),
    }
    today = date.today()
    vocabulary = config.vocabulary
    memberships = []
    for a in links:
        if a["predicate"] != "member_of" or a["status"] != "accepted":
            continue
        group = units.get(a["object_id"]) or {"name": a["object_id"], "kind": None}
        view = groups.membership_view(a, vocabulary, today)
        memberships.append(
            {
                "unit": group["name"],
                "unit_id": a["object_id"],
                "membership_id": a["id"],
                "group_slug": group.get("slug"),
                "group_status": group.get("status"),
                "kind": group["kind"],
                "kind_label": vocabulary.kind_label(group["kind"]) if group["kind"] else None,
                **{k: view[k] for k in ("role", "role_label", "leader", "title", "start", "end")},
                "source": view["source"],
                "current": view["current"],
            }
        )
    memberships.sort(key=lambda m: (m.get("start") or "", m["unit"]), reverse=True)
    memberships.sort(key=lambda m: not m["current"])  # current first, latest start first
    merges = active_merges(engine, person_id)
    merged_into = None
    if row["status"] == "merged":
        into = next((m for m in merges if m["merged_id"] == person_id), None)
        if into is not None:
            merged_into = {
                "id": into["survivor_id"],
                "name": store.display_names(engine, [into["survivor_id"]]).get(into["survivor_id"]),
                "merge_id": into["merge_id"],
                "at": into["at"],
                "actor": into["actor"],
            }
    # Timeline: accepted papers and likely suggestions per year.
    likely = [
        a["object_id"].removeprefix("pub:")
        for a in authored
        if a["status"] == "accepted"
        or (a["status"] == "suggested" and (a["score"] or 0) >= config.evidence.review_threshold)
    ]
    summaries = publication_summaries(index_of(request), lake_of(request), likely)
    by_year: Counter = Counter()
    confirmed: Counter = Counter()
    accepted = {a["object_id"].removeprefix("pub:") for a in authored if a["status"] == "accepted"}
    for pmid in likely:
        year = (summaries.get(pmid) or {}).get("year")
        if year:
            by_year[year] += 1
            if pmid in accepted:
                confirmed[year] += 1
    try:
        concepts = index_of(request).summaries([person_id]).get(person_id, {}).get("concepts", [])
    except Exception:
        concepts = []
    return envelope(
        {
            "id": row["id"],
            "name": row["display_name"],
            "status": row["status"],
            "family_name": row["family_name"],
            "given_name": row["given_name"],
            "names": names,
            "identifiers": identifiers,
            "units": memberships,
            "merged_into": merged_into,
            "public": bool(row.get("public")),
            "merged": [m for m in merges if m["survivor_id"] == person_id],
            "can_curate": principal_of(request).can_curate(engine, person_id),
            "counts": counts,
            "concepts": concepts,
            "timeline": [
                {"year": y, "publications": by_year[y], "accepted": confirmed[y]}
                for y in sorted(by_year)
            ],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )


@router.get("/{person_id}/publications", summary="A person's publications and suggestions")
def person_publications(
    request: Request,
    person_id: str,
    p: Annotated[Page, Depends(page)],
    status: Annotated[
        list[str] | None, Query(description="accepted, suggested, deferred, rejected")
    ] = None,
    band_: Annotated[
        list[str] | None, Query(alias="band", description="strong, possible, unlikely")
    ] = None,
    sort: str = Query("year", description="year | score"),
) -> dict[str, Any]:
    config = config_of(request)
    _person_row(request, person_id)
    statuses = split_values(status) or ["accepted", "suggested"]
    bands = split_values(band_) or ["strong", "possible"]
    bad = (set(statuses) - set(PUBLICATION_STATUSES)) | (set(bands) - set(BANDS))
    if bad:
        raise HTTPException(status_code=400, detail=f"Unknown status or band: {sorted(bad)}")
    rows = store.assertions_for(engine_of(request), subject_id=person_id, predicate="authored")
    tabs = Counter(
        "accepted"
        if a["status"] == "accepted"
        else f"suggested:{band(a['score'], config)}"
        if a["status"] == "suggested"
        else a["status"]
        for a in rows
    )
    chosen = [
        a
        for a in rows
        if a["status"] in statuses
        and (a["status"] != "suggested" or band(a["score"], config) in bands)
    ]
    summaries = publication_summaries(
        index_of(request), lake_of(request), [a["object_id"].removeprefix("pub:") for a in chosen]
    )

    def year_of(a: dict[str, Any]) -> int:
        return (summaries.get(a["object_id"].removeprefix("pub:")) or {}).get("year") or 0

    if sort == "score":
        chosen.sort(key=lambda a: (-(a["score"] or 0), -year_of(a)))
    else:
        chosen.sort(key=lambda a: (-year_of(a), -(a["score"] or 0)))
    window = chosen[p.offset : p.offset + p.limit]
    items = [
        {
            "assertion": assertion_view(a, config),
            "publication": summaries.get(
                a["object_id"].removeprefix("pub:"), {"pmid": a["object_id"].removeprefix("pub:")}
            ),
        }
        for a in window
    ]
    return envelope(
        items,
        total=len(chosen),
        limit=p.limit,
        next_cursor=p.next_cursor(len(chosen)),
        tabs=dict(tabs),
    )


class ManualPublication(BaseModel):
    pmid: str = Field(pattern=r"^[1-9][0-9]{0,8}$", description="PubMed ID")
    note: str | None = Field(None, max_length=1000)


@router.post(
    "/{person_id}/publications",
    status_code=201,
    summary="Add a missing publication to a person (fetched from PubMed if needed)",
)
def add_publication(request: Request, person_id: str, body: ManualPublication) -> dict[str, Any]:
    from tessera.connectors.base import IngestOptions
    from tessera.jobs.ingest import ingest

    config = config_of(request)
    engine = engine_of(request)
    _person_row(request, person_id)
    actor = require_curate(request, person_id).name
    lake = lake_of(request)
    lake.refresh(force=True)
    if not lake.scalar("SELECT count(*) FROM pubmed_article WHERE pmid = ?", [body.pmid]):
        result = ingest(
            config,
            request.app.state.home,
            engine,
            "pubmed",
            IngestOptions(mode="ids", ids=[body.pmid]),
        )
        lake.refresh(force=True)
        if result.status == "failed" or not lake.scalar(
            "SELECT count(*) FROM pubmed_article WHERE pmid = ?", [body.pmid]
        ):
            raise HTTPException(status_code=404, detail=f"PubMed has no record {body.pmid}.")
    now = utc_now()
    object_id = f"pub:{body.pmid}"
    with engine.begin() as conn:
        existing = (
            conn.execute(
                select(assertion).where(
                    assertion.c.subject_id == person_id,
                    assertion.c.predicate == "authored",
                    assertion.c.object_id == object_id,
                )
            )
            .mappings()
            .first()
        )
        if existing is not None:
            aid = existing["id"]
        else:
            aid = new_id("asr")
            conn.execute(
                assertion.insert().values(
                    id=aid,
                    subject_type="person",
                    subject_id=person_id,
                    predicate="authored",
                    object_type="publication",
                    object_id=object_id,
                    qualifier_key="",
                    qualifiers={},
                    origin="human",
                    method="manual/v1",
                    score=None,
                    evidence=[
                        {
                            "feature": "manual",
                            "value": actor,
                            "weight": None,
                            "explanation": f"Added by {actor}",
                            "source_ref": object_id,
                        }
                    ],
                    status="suggested",
                    locked=False,
                    reported=True,
                    first_seen_at=now,
                    last_evaluated_at=now,
                    updated_at=now,
                )
            )
    if existing is None or existing["status"] != "accepted":
        store.decide(engine, aid, "accept", actor, body.note)
    refresh_index(request, people=[person_id], records=[object_id])
    row = store.assertions_for(
        engine, subject_id=person_id, predicate="authored", reported_only=False
    )
    created = next(r for r in row if r["id"] == aid)
    return envelope(assertion_view(created, config))


def _linked(request: Request, person_id: str, predicate: str) -> list[dict[str, Any]]:
    _person_row(request, person_id)
    rows = [
        a
        for a in store.assertions_for(engine_of(request), subject_id=person_id, predicate=predicate)
        if a["status"] != "rejected"
    ]
    summaries = index_of(request).summaries([a["object_id"] for a in rows])
    config = config_of(request)
    return [
        {
            "assertion": assertion_view(a, config),
            "summary": summaries.get(a["object_id"], {"id": a["object_id"]}),
        }
        for a in rows
    ]


@router.get("/{person_id}/grants", summary="Grants the person is an investigator on")
def person_grants(request: Request, person_id: str) -> dict[str, Any]:
    items = _linked(request, person_id, "investigator_on")
    items.sort(key=lambda i: -((i["summary"].get("fiscal_years") or [0, 0])[1]))
    return envelope(items, total=len(items))


@router.get("/{person_id}/studies", summary="Studies the person may be an official of")
def person_studies(request: Request, person_id: str) -> dict[str, Any]:
    items = _linked(request, person_id, "official_of")
    items.sort(key=lambda i: -(i["assertion"]["score"] or 0))
    return envelope(items, total=len(items))


@router.get("/{person_id}/history", summary="Curation decisions about this person's links")
def person_history(
    request: Request, person_id: str, limit: int = Query(50, ge=1, le=500)
) -> dict[str, Any]:
    _person_row(request, person_id)
    with engine_of(request).connect() as conn:
        rows = (
            conn.execute(
                select(curation_event, assertion.c.predicate, assertion.c.object_id)
                .join(assertion, assertion.c.id == curation_event.c.assertion_id)
                .where(assertion.c.subject_id == person_id)
                .order_by(curation_event.c.id.desc())
                .limit(limit)
            )
            .mappings()
            .all()
        )
    return envelope([dict(r) for r in rows])


__all__ = ["router"]


class PublicIn(BaseModel):
    public: bool = Field(description="Whether the person has a profile on the public site")


@router.post("/{person_id}/public", summary="Give the person a public profile, or take it away")
def set_public(request: Request, person_id: str, body: PublicIn) -> dict[str, Any]:
    """The person, someone acting for them, a curator for them, or an admin (PRF-03)."""
    from tessera.profiles import store as profiles
    from tessera.profiles.sections import ProfileError

    row = _person_row(request, person_id)
    principal = principal_of(request)
    capacity = profiles.capacity(principal, engine_of(request), person_id)
    if capacity is None:
        raise HTTPException(status_code=403,
                            detail="You may decide only for the people in your scope.")  # fmt: skip
    if row["status"] not in ("active", "former"):
        raise HTTPException(status_code=409, detail="Only people in the directory have profiles.")
    try:
        out = profiles.set_public(engine_of(request), person_id, actor=principal.name,
                                  capacity=capacity, public=body.public)  # fmt: skip
    except ProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return envelope({"id": person_id, "public": out["public"]})
