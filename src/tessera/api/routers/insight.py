"""Insight: metric definitions, figures over a cohort and a period, and saved cohorts.

Every figure answers with what it counted (INT-01): the metric's definition and version, the
cohort, the period, the options, notes, the cells that are small numbers, and the sources with
their data timestamps; ``records=true`` adds the records counted and the links behind them.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from tessera.api.access import principal_of
from tessera.api.common import config_of, engine_of, envelope, index_of, lake_of
from tessera.connectors.reporter import fiscal_year
from tessera.core.clock import isoformat
from tessera.core.errors import TesseraError
from tessera.core.once import Once
from tessera.insight import cohorts, export, metrics, reports
from tessera.insight.figure import Period, PeriodError
from tessera.insight.inputs import coverage
from tessera.insight.run import figure as make_figure

router = APIRouter(prefix="/api/v1/insight", tags=["insight"])

RESERVED = {"cohort", "start", "end", "records"}


def _fail(exc: TesseraError) -> HTTPException:
    message = str(exc)
    missing = message.startswith(
        ("There is no metric", "No saved cohort", "No group", "No report", "No snapshot")
    )
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
    key = None if records else _figure_key(request, metric_id, cohort, start, end, options)
    if key is not None and (kept := _recall_figure(key)) is not None:
        return envelope(kept)

    def build() -> dict[str, Any]:
        if key is not None and (kept := _recall_figure(key)) is not None:
            return kept  # built while this request waited its turn
        try:
            result = make_figure(
                engine_of(request), lake_of(request), config_of(request), metric_id,
                cohort=cohort, start=start, end=end, options=options, search=_search(request),
            )  # fmt: skip
        except (TesseraError, PeriodError) as exc:
            raise _fail(exc) from exc
        body = result.to_json(records=records)
        if key is not None:
            _remember_figure(key, body)
        return body

    # Everyone asking for this figure now waits for one build of it, rather than each
    # building it at once (with its records, each request builds its own).
    return envelope(build() if key is None else _building.run(key, build))


# Finished figures, as served, until the data changes (the same version as the inputs cache),
# the day turns, or a saved cohort is edited; never with their records, which can be large.
FIGURES_KEPT = 256
FIGURE_SECONDS = 600
_figures: OrderedDict[tuple[Any, ...], tuple[float, dict[str, Any]]] = OrderedDict()
_figures_lock = threading.Lock()
_building = Once()


def _figure_key(request: Request, metric_id: str, cohort: str | None, start: int | None,
                end: int | None, options: dict[str, str]) -> tuple[Any, ...]:  # fmt: skip
    from sqlalchemy import func, select

    from tessera.insight.inputs import data_version
    from tessera.ledger.tables import cohort as cohort_table

    engine = engine_of(request)
    with engine.connect() as conn:
        saved = conn.execute(select(func.max(cohort_table.c.updated_at))).scalar()
    return (
        str(engine.url),
        str(lake_of(request).root),
        metric_id,
        cohort,
        start,
        end,
        tuple(sorted(options.items())),
        date.today().isoformat(),
        str(saved),
        data_version(engine),
    )


def _recall_figure(key: tuple[Any, ...]) -> dict[str, Any] | None:
    with _figures_lock:
        found = _figures.get(key)
        if found is None or time.monotonic() - found[0] > FIGURE_SECONDS:
            return None
        _figures.move_to_end(key)
        return found[1]


def _remember_figure(key: tuple[Any, ...], body: dict[str, Any]) -> None:
    with _figures_lock:
        _figures[key] = (time.monotonic(), body)
        _figures.move_to_end(key)
        while len(_figures) > FIGURES_KEPT:
            _figures.popitem(last=False)


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
                conn, body.name, spec, actor=principal.name, description=body.description,
                owner=_owner(principal),
            )  # fmt: skip
            row = cohorts.get_cohort(conn, cohort_id)
    except TesseraError as exc:
        raise _fail(exc) from exc
    return envelope(_saved_view(row))


def _owner(principal: Any) -> str:
    return principal.account_id or "operator"


def _may_change(request: Request, row: dict[str, Any]) -> None:
    """Whoever saved it (by account, since names are not unique), or an admin."""
    principal = principal_of(request)
    mine = row.get("owner") is not None and row["owner"] == _owner(principal)
    if not (principal.is_admin or principal.single_operator or mine):
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


# --- exports ------------------------------------------------------------------------------------


def _download(content: bytes, media: str, filename: str) -> Response:
    return Response(
        content, media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )  # fmt: skip


@router.get(
    "/figures/{metric_id}/export",
    summary="A figure with its records and definitions, as CSV, Parquet, or JSON",
    response_class=Response,
)
def export_figure(
    request: Request,
    metric_id: str,
    format: str = Query("csv", description="csv (a zip), parquet (a zip), or json"),
    cohort: str | None = Query(None, max_length=4000),
    start: int | None = None,
    end: int | None = None,
) -> Response:
    options = {k: v for k, v in request.query_params.items() if k not in RESERVED | {"format"}}
    try:
        result = make_figure(
            engine_of(request), lake_of(request), config_of(request), metric_id,
            cohort=cohort, start=start, end=end, options=options, search=_search(request),
        )  # fmt: skip
        payload = result.to_json(records=True)
        filters = {"cohort": cohort or "organization", "start": start, "end": end, **options}
        return _download(*export.figure_export(payload, format, filters=filters))
    except TesseraError as exc:
        raise _fail(exc) from exc


# --- reports and snapshots ----------------------------------------------------------------------


def _report_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "cohort": row["cohort"],
        "start": row["start_year"],
        "end": row["end_year"],
        "figures": row["figures"],
        "snapshots": row.get("snapshots"),
        "created_by": row["created_by"],
        "created_at": isoformat(row["created_at"]),
        "updated_by": row["updated_by"],
        "updated_at": isoformat(row["updated_at"]),
    }


def _snapshot_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "report_id": row["report_id"],
        "name": row["name"],
        "note": row["note"],
        "taken_by": row["taken_by"],
        "taken_at": isoformat(row["taken_at"]),
        "digest": row["digest"],
        "summary": row["summary"],
    }


class FigureIn(BaseModel):
    metric: str = Field(min_length=1, max_length=80)
    options: dict[str, str] = Field(default_factory=dict)


class ReportIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=4000)
    cohort: str = Field("organization", min_length=1, max_length=4000)
    start: int | None = None
    end: int | None = None
    figures: list[FigureIn] = Field(min_length=1, max_length=reports.MAX_FIGURES)


class ReportChange(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=4000)
    cohort: str | None = Field(None, min_length=1, max_length=4000)
    start: int | None = None
    end: int | None = None
    figures: list[FigureIn] | None = Field(None, min_length=1, max_length=reports.MAX_FIGURES)


class SnapshotIn(BaseModel):
    note: str | None = Field(None, max_length=2000)


@router.get("/reports", summary="Reports: named sets of figures over a cohort and a period")
def list_reports(request: Request) -> dict[str, Any]:
    with engine_of(request).connect() as conn:
        return envelope([_report_view(r) for r in reports.list_reports(conn)])


@router.post("/reports", summary="Save a report for everyone who reads Insight")
def create_report(request: Request, body: ReportIn) -> dict[str, Any]:
    principal = principal_of(request)
    actor = principal.name
    try:
        cohorts.resolve(engine_of(request), body.cohort, search=_search(request))  # it must resolve
        with engine_of(request).begin() as conn:
            report_id = reports.create_report(
                conn, name=body.name, description=body.description, cohort=body.cohort,
                start=body.start, end=body.end, actor=actor, owner=_owner(principal),
                figures=[f.model_dump() for f in body.figures],
            )  # fmt: skip
            row = reports.get_report(conn, report_id)
    except TesseraError as exc:
        raise _fail(exc) from exc
    return envelope(_report_view(row))


@router.get("/reports/{report_id}", summary="A report's definition and its snapshots")
def get_report(request: Request, report_id: str) -> dict[str, Any]:
    try:
        with engine_of(request).connect() as conn:
            row = reports.get_report(conn, report_id)
            snaps = reports.list_snapshots(conn, report_id)
    except TesseraError as exc:
        raise _fail(exc) from exc
    return envelope({**_report_view(row), "snapshot_list": [_snapshot_view(s) for s in snaps]})


@router.post("/reports/{report_id}", summary="Change a report")
def change_report(request: Request, report_id: str, body: ReportChange) -> dict[str, Any]:
    actor = principal_of(request).name
    given = body.model_fields_set
    try:
        if body.cohort:
            cohorts.resolve(engine_of(request), body.cohort, search=_search(request))
        with engine_of(request).begin() as conn:
            _may_change(request, reports.get_report(conn, report_id))
            reports.update_report(
                conn, report_id, actor=actor, name=body.name, description=body.description,
                cohort=body.cohort,
                figures=[f.model_dump() for f in body.figures] if body.figures else None,
                start=body.start if "start" in given else "keep",
                end=body.end if "end" in given else "keep",
            )  # fmt: skip
            row = reports.get_report(conn, report_id)
    except TesseraError as exc:
        raise _fail(exc) from exc
    return envelope(_report_view(row))


@router.delete("/reports/{report_id}", summary="Delete a report (its snapshots stay)")
def delete_report(request: Request, report_id: str) -> dict[str, Any]:
    try:
        with engine_of(request).begin() as conn:
            _may_change(request, reports.get_report(conn, report_id))
            reports.delete_report(conn, report_id)
    except TesseraError as exc:
        raise _fail(exc) from exc
    return envelope({"deleted": report_id})


@router.post(
    "/reports/{report_id}/snapshots",
    summary="Freeze a report: its figures and the exact inputs they were computed from",
)
def take_snapshot(request: Request, report_id: str, body: SnapshotIn) -> dict[str, Any]:
    try:
        snapshot_id = reports.take_snapshot(
            engine_of(request), lake_of(request), config_of(request), request.app.state.home,
            report_id, actor=principal_of(request).name, note=body.note,
            search=_search(request),
        )  # fmt: skip
        with engine_of(request).connect() as conn:
            row = reports.get_snapshot(conn, snapshot_id)
    except TesseraError as exc:
        raise _fail(exc) from exc
    return envelope(_snapshot_view(row))


@router.get("/snapshots", summary="Every snapshot, newest first")
def list_snapshots(request: Request) -> dict[str, Any]:
    with engine_of(request).connect() as conn:
        return envelope([_snapshot_view(r) for r in reports.list_snapshots(conn)])


def _snapshot(request: Request, snapshot_id: str) -> dict[str, Any]:
    try:
        with engine_of(request).connect() as conn:
            return reports.get_snapshot(conn, snapshot_id)
    except TesseraError as exc:
        raise _fail(exc) from exc


@router.get("/snapshots/{snapshot_id}", summary="A snapshot: its manifest and frozen figures")
def get_snapshot(
    request: Request,
    snapshot_id: str,
    records: bool = Query(False, description="Include each figure's records"),
) -> dict[str, Any]:
    row = _snapshot(request, snapshot_id)
    try:
        manifest, figures = reports.load_snapshot(request.app.state.home, row)
    except TesseraError as exc:
        raise _fail(exc) from exc
    if not records:
        figures = [{k: v for k, v in f.items() if k not in ("records", "record_columns")}
                   for f in figures]  # fmt: skip
    return envelope({**_snapshot_view(row), "manifest": manifest, "figures": figures})


@router.get(
    "/snapshots/{snapshot_id}/verify",
    summary="Compute a snapshot's figures again from its frozen inputs and compare",
)
def verify_snapshot(request: Request, snapshot_id: str) -> dict[str, Any]:
    row = _snapshot(request, snapshot_id)
    try:
        return envelope(reports.verify_snapshot(request.app.state.home, row))
    except TesseraError as exc:
        raise _fail(exc) from exc


@router.get(
    "/snapshots/{snapshot_id}/export",
    summary="A snapshot's figures with their records, definitions, and manifest (a zip)",
    response_class=Response,
)
def export_snapshot(
    request: Request,
    snapshot_id: str,
    format: str = Query("csv", description="csv, parquet, or json"),
    figure: int | None = Query(None, ge=1, description="Only this figure (1, 2, …)"),
) -> Response:
    row = _snapshot(request, snapshot_id)
    try:
        manifest, figures = reports.load_snapshot(request.app.state.home, row)
        if figure is not None:
            if figure > len(figures):
                raise HTTPException(status_code=404, detail=f"The snapshot has no figure {figure}.")
            report_ = manifest["report"]
            payload = figures[figure - 1]
            filters = {"cohort": report_["cohort"], "start": report_["start"],
                       "end": report_["end"], **export.option_values(payload)}  # fmt: skip
            return _download(*export.figure_export(payload, format, filters=filters,
                                                   snapshot=snapshot_id))  # fmt: skip
        return _download(*export.snapshot_export(manifest, figures, format))
    except TesseraError as exc:
        raise _fail(exc) from exc


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
