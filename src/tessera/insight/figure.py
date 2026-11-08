"""Periods and the figure envelope.

A figure carries everything needed to read it honestly (INT-01): the metric's definition and
version, the cohort and its size, the period, the options chosen, the columns with their units,
notes, the cells that are small numbers, the sources with their data timestamps, and, on
request, the records counted and why each is in the cohort.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pyarrow as pa

from tessera.core.clock import isoformat
from tessera.core.errors import TesseraError
from tessera.insight.metrics import Metric

EARLIEST_YEAR = 1950
MAX_SPAN = 50
COUNT_KINDS = frozenset({"count"})  # columns that small-number marks apply to


class PeriodError(TesseraError):
    """A period that cannot be used."""


@dataclass(frozen=True)
class Period:
    """Inclusive years: calendar years, or the funder's fiscal years (``fiscal``)."""

    start: int
    end: int
    basis: str = "calendar"

    @property
    def label(self) -> str:
        prefix = "FY" if self.basis == "fiscal" else ""
        if self.start == self.end:
            return f"{prefix}{self.start}"
        return f"{prefix}{self.start}\u2013{prefix}{self.end}"  # an en dash

    @property
    def years(self) -> range:
        return range(self.start, self.end + 1)

    def __contains__(self, year: object) -> bool:
        return isinstance(year, int) and self.start <= year <= self.end

    def to_json(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "basis": self.basis, "label": self.label}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Period:
        return cls(int(data["start"]), int(data["end"]), str(data.get("basis", "calendar")))

    @classmethod
    def make(
        cls,
        start: int | None,
        end: int | None,
        basis: str = "calendar",
        *,
        today: date | None = None,
        years: int = 10,
        earliest: int | None = None,
    ) -> Period:
        """A checked period; by default the ten years up to this one, or fewer when the data
        is complete only from ``earliest``."""
        today = today or date.today()
        latest = today.year + 5
        end = end if end is not None else today.year
        if start is None:
            start = end - years + 1
            if earliest is not None:
                start = min(max(start, earliest), end)
        if basis not in ("calendar", "fiscal"):
            raise PeriodError("A period is in calendar or fiscal years.")
        if not (EARLIEST_YEAR <= start <= latest and EARLIEST_YEAR <= end <= latest):
            raise PeriodError(f"Years are between {EARLIEST_YEAR} and {latest}.")
        if start > end:
            raise PeriodError("The period starts after it ends.")
        if end - start + 1 > MAX_SPAN:
            raise PeriodError(f"A period covers at most {MAX_SPAN} years.")
        return cls(start, end, basis)


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    kind: str = "count"  # label, year, count, money, share, date, text, ref, list
    unit: str | None = None  # papers, grants, dollars, studies

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"key": self.key, "label": self.label, "kind": self.kind}
        if self.unit:
            out["unit"] = self.unit
        return out


@dataclass(frozen=True)
class FigureRequest:
    """What a figure is asked for, independent of where its inputs came from."""

    metric: Metric
    cohort: dict[str, Any]  # the cohort's description (Cohort.to_json)
    period: Period
    options: dict[str, str]
    small_number: int
    generated_at: datetime
    today: date

    def to_json(self) -> dict[str, Any]:
        return {
            "metric": self.metric.ref,
            "cohort": self.cohort,
            "period": self.period.to_json(),
            "options": self.options,
            "small_number": self.small_number,
            "generated_at": isoformat(self.generated_at),
            "today": self.today.isoformat(),
        }


@dataclass
class Figure:
    request: FigureRequest
    columns: list[Column]
    rows: list[dict[str, Any]]
    totals: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    record_columns: list[Column] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    period_view: dict[str, Any] | None = None  # for figures about what is coming, not years

    @property
    def metric(self) -> Metric:
        return self.request.metric

    def small_cells(self) -> list[dict[str, Any]]:
        """Counts above zero and below the small-number threshold, by row and column."""
        threshold = self.request.small_number
        keys = [c.key for c in self.columns if c.kind in COUNT_KINDS]
        out = []
        for i, row in enumerate(self.rows):
            small = [k for k in keys if isinstance(row.get(k), int) and 0 < row[k] < threshold]
            if small:
                out.append({"row": i, "columns": small})
        return out

    def about(self) -> dict[str, Any]:
        """Everything but the numbers: what the figure is, over whom, when, and from what."""
        options = {
            o.key: {"value": self.request.options[o.key], "label": o.label_of(
                self.request.options[o.key])}
            for o in self.metric.options
            if o.key in self.request.options
        }  # fmt: skip
        return {
            "metric": self.metric.to_json(),
            "cohort": self.request.cohort,
            "period": self.period_view or self.request.period.to_json(),
            "options": options,
            "small_number": self.request.small_number,
            "notes": self.notes,
            "sources": self.sources,
            "generated_at": isoformat(self.request.generated_at),
        }

    def to_json(self, *, records: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            **self.about(),
            "columns": [c.to_json() for c in self.columns],
            "rows": self.rows,
            "totals": self.totals,
            "small": self.small_cells(),
            "record_count": len(self.records),
        }
        if records:
            out["record_columns"] = [c.to_json() for c in self.record_columns]
            out["records"] = self.records
        return out

    def table(self) -> pa.Table:
        return _arrow(self.columns, self.rows)

    def records_table(self) -> pa.Table:
        return _arrow(self.record_columns, self.records)


def _arrow(columns: list[Column], rows: list[dict[str, Any]]) -> pa.Table:
    data: dict[str, list[Any]] = {c.key: [] for c in columns}
    for row in rows:
        for c in columns:
            value = row.get(c.key)
            data[c.key].append("; ".join(map(str, value)) if isinstance(value, list) else value)
    return pa.table(data) if columns else pa.table({})


def share(part: float, whole: float) -> float | None:
    return round(part / whole, 4) if whole else None


__all__ = ["Column", "Figure", "FigureRequest", "Period", "PeriodError", "share"]
