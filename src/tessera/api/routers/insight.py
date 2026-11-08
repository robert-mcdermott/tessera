"""Insight: metric definitions, figures over a cohort and a period, and saved cohorts.

Every figure answers with what it counted (INT-01): the metric's definition and version, the
cohort, the period, the options, notes, the cells that are small numbers, and the sources with
their data timestamps; ``records=true`` adds the records counted and the links behind them.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from tessera.api.access import principal_of
from tessera.api.common import config_of, engine_of, envelope, index_of, lake_of
from tessera.connectors.reporter import fiscal_year
from tessera.core.clock import isoformat
from tessera.core.errors import TesseraError
from tessera.insight import cohorts, metrics
from tessera.insight.figure import Period, PeriodError
from tessera.insight.inputs import coverage
from tessera.insight.run import figure as make_figure

router = APIRouter(prefix="/api/v1/insight", tags=["insight"])

RESERVED = {"cohort", "start", "end", "records"}


def _fail(exc: TesseraError) -> HTTPException:
    message = str(exc)
    missing = message.startswith(("There is no metric", "No saved cohort", "No group"))
    return HTTPException(status_code=404 if missing else 400, detail=message)


@router.get("/metrics", summary="Every metric Insight shows, with its definition and version")
def list_metrics() -> dict[str, Any]:
    return envelope([m.to_json() for m in metrics.all_metrics()])


@router.get(
    "/figures/{metric_id}",
    summary="A figure: a metric over a cohort and a period, with what it counted",
)
def get_figure(
    request: Request,
    metric_id: str,
    cohort: str | None = Query(
        None,
        max_length=4000,
        description="organization (default), group:<address>[+below][;current], "
        "people:<ID>,<ID>, search:<words>, or a saved cohort's ID",
    ),
    start: int | None = Query(None, description="First year (calendar or fiscal, by metric)"),
    end: int | None = Query(None, description="Last year"),
    records: bool = Query(False, description="Include the records counted"),
) -> dict[str, Any]:
    options = {k: v for k, v in request.query_params.items() if k not in RESERVED}
    try:
        result = make_figure(
            engine_of(request), lake_of(request), config_of(request), metric_id,
            cohort=cohort, start=start, end=end, options=options, search=_search(request),
        )  # fmt: skip
    except (TesseraError, PeriodError) as exc:
        raise _fail(exc) from exc
    return envelope(result.to_json(records=records))


def _search(request: Request) -> Any:
    try:
        index = index_of(request)
        return index if index.path.is_file() else None
    except Exception:  # pragma: no cover - the index is optional here
        return None


@router.get("/cohort", summary="Who a cohort is: its label, size, and members")
def preview_cohort(
    request: Request,
    cohort: str | None = Query(None, max_length=4000),
    limit: int = Query(200, ge=0, le=2000),
) -> dict[str, Any]:
    try:
        resolved = cohorts.resolve(engine_of(request), cohort, search=_search(request))
    except TesseraError as exc:
        raise _fail(exc) from exc
    members = sorted(resolved.members.values(), key=lambda m: m.name.lower())
    return envelope(
        {
            **resolved.to_json(),
            "members": [
                {
                    "id": m.person_id,
                    "name": m.name,
                    "start_year": m.start_year,
                    "end_year": m.end_year,
                }
                for m in members[:limit]
            ],
        }
    )


def _saved_view(row: dict[str, Any]) -> dict[str, Any]:
    spec = cohorts.CohortSpec.from_json(row["definition"])
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "definition": spec.to_json(),
        "spec": spec.text(),
        "created_by": row["created_by"],
        "created_at": isoformat(row["created_at"]),
        "updated_by": row["updated_by"],
        "updated_at": isoformat(row["updated_at"]),
    }


@router.get("/cohorts", summary="Saved cohorts")
def list_saved(request: Request) -> dict[str, Any]:
    with engine_of(request).connect() as conn:
        return envelope([_saved_view(r) for r in cohorts.list_cohorts(conn)])


class CohortIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    cohort: str = Field(
        min_length=1, max_length=4000, description="A cohort as written in an address"
    )


class CohortChange(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    cohort: str | None = Field(None, min_length=1, max_length=4000)


def _spec(text: str) -> cohorts.CohortSpec:
    found = cohorts.parse(text)
    if isinstance(found, str):
        raise cohorts.CohortError("A saved cohort is made from a cohort, not another saved one.")
    return found


@router.post("/cohorts", summary="Save a cohort under a name, for everyone to use")
def save_cohort(request: Request, body: CohortIn) -> dict[str, Any]:
    principal = principal_of(request)
    engine = engine_of(request)
    try:
        spec = _spec(body.cohort)
        cohorts.resolve(engine, spec, search=_search(request))  # it must resolve now
        with engine.begin() as conn:
            cohort_id = cohorts.save_cohort(
                conn, body.name, spec, actor=principal.name, description=body.description
            )
            row = cohorts.get_cohort(conn, cohort_id)
    except TesseraError as exc:
        raise _fail(exc) from exc
    return envelope(_saved_view(row))


def _may_change(request: Request, row: dict[str, Any]) -> None:
    principal = principal_of(request)
    if not (principal.is_admin or principal.single_operator or row["created_by"] == principal.name):
        raise HTTPException(
            status_code=403, detail="Only whoever saved a cohort, or an admin, may change it."
        )


@router.post("/cohorts/{cohort_id}", summary="Rename or redefine a saved cohort")
def change_cohort(request: Request, cohort_id: str, body: CohortChange) -> dict[str, Any]:
    principal = principal_of(request)
    engine = engine_of(request)
    try:
        with engine.begin() as conn:
            _may_change(request, cohorts.get_cohort(conn, cohort_id))
            spec = _spec(body.cohort) if body.cohort else None
            if spec is not None:
                cohorts.resolve(engine, spec, search=_search(request))
            cohorts.update_cohort(
                conn, cohort_id, actor=principal.name, name=body.name, spec=spec,
                description=body.description,
            )  # fmt: skip
            row = cohorts.get_cohort(conn, cohort_id)
    except TesseraError as exc:
        raise _fail(exc) from exc
    return envelope(_saved_view(row))


@router.delete("/cohorts/{cohort_id}", summary="Delete a saved cohort")
def delete_cohort(request: Request, cohort_id: str) -> dict[str, Any]:
    try:
        with engine_of(request).begin() as conn:
            _may_change(request, cohorts.get_cohort(conn, cohort_id))
            cohorts.delete_cohort(conn, cohort_id)
    except TesseraError as exc:
        raise _fail(exc) from exc
    return envelope({"deleted": cohort_id})


@router.get("/years", summary="The years Insight can show, and the period it starts with")
def data_years(request: Request) -> dict[str, Any]:
    lake, config = lake_of(request), config_of(request)
    try:
        first = lake.scalar("SELECT min(pub_year) FROM pubmed_article WHERE pub_year > 1900")
        fiscal = lake.scalar("SELECT min(fiscal_year) FROM reporter_project")
    except Exception:  # pragma: no cover - an empty lake
        first = fiscal = None
    covered = coverage(engine_of(request), config)
    today = date.today()

    def span(first_year: int | None, complete: int | None, last: int, now: int) -> dict[str, Any]:
        default = Period.make(None, now, today=today, earliest=complete)
        low = min(first_year or default.start, default.start)
        return {"first": low, "last": last, "complete_from": complete,
                "default": {"start": default.start, "end": default.end}}  # fmt: skip

    return envelope(
        {
            "today": today.isoformat(),
            "calendar": span(first, covered.get("pubmed"), today.year, today.year),
            "fiscal": span(fiscal, covered.get("reporter"), today.year + 1, fiscal_year(today)),
        }
    )


__all__ = ["router"]
