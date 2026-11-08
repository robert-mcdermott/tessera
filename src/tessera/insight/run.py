"""Making figures: resolve the cohort, gather the inputs, compute.

``figure`` does all three against the live lake and ledger. ``compute`` is the last step on
its own, for inputs gathered earlier (a snapshot's).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from sqlalchemy.engine import Engine

from tessera.config import Config
from tessera.connectors.reporter import fiscal_year
from tessera.core.clock import utc_now
from tessera.insight import cohorts, metrics
from tessera.insight.figure import Figure, FigureRequest, Period
from tessera.insight.inputs import Context, Inputs, gather, source_stamps
from tessera.lake import Lake


def compute(
    metric: metrics.Metric,
    inputs: Inputs,
    cohort: dict[str, Any],
    period: Period,
    options: dict[str, str],
    *,
    small_number: int,
    today: date,
    generated_at: datetime | None = None,
) -> Figure:
    assert metric.compute is not None
    request = FigureRequest(
        metric, cohort, period, options, small_number, generated_at or utc_now(), today
    )
    result = metric.compute(inputs, request)
    result.sources = inputs.sources_for(metric.sources)
    if metric.period != "upcoming":
        result.notes += coverage_notes(result.sources, period)
    return result


def coverage_notes(sources: list[dict[str, Any]], period: Period) -> list[str]:
    """Say so when the period reaches back before a source's records are complete."""
    notes = []
    for s in sources:
        first = s.get("complete_from")
        if first is not None and period.start < first:
            year = f"FY{first}" if s.get("period") == "fiscal" else str(first)
            notes.append(
                f"{s['label']} records were collected from {year} on (the first sync reaches "
                "back a set number of years); earlier years are incomplete."
            )
    return notes


def earliest(sources: list[dict[str, Any]]) -> int | None:
    years = [s["complete_from"] for s in sources if s.get("complete_from") is not None]
    return max(years) if years else None


def figure(
    engine: Engine,
    lake: Lake,
    config: Config,
    metric_id: str,
    *,
    cohort: str | cohorts.CohortSpec | None = None,
    start: int | None = None,
    end: int | None = None,
    options: Mapping[str, str | None] | None = None,
    today: date | None = None,
    search: Any = None,
) -> Figure:
    today = today or date.today()
    metric = metrics.get(metric_id)
    chosen = metric.resolve_options(options or {})
    stamps = source_stamps(engine, metric.sources, config)
    basis = "fiscal" if metric.period == "fiscal" else "calendar"
    if end is None and basis == "fiscal":
        end = fiscal_year(today)  # the fiscal year under way (from October)
    period = Period.make(start, end, basis, today=today, earliest=earliest(stamps))
    resolved = cohorts.resolve(engine, cohort, today=today, search=search)
    inputs = gather(Context(engine, lake, config, resolved), metric.needs)
    return compute(
        metric, inputs, resolved.to_json(), period, chosen,
        small_number=config.insight.small_number, today=today,
    )  # fmt: skip


__all__ = ["compute", "coverage_notes", "earliest", "figure"]
