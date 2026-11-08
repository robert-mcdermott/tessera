"""Metric definitions (INT-01).

Every number in Insight is a metric over a cohort and a period. A metric is defined once, here,
with an identifier and a version (``publications.count/v1``), a plain definition, its counting
rule, its unit, and its sources. The definition travels with every figure, export, and
snapshot. A change to what a metric counts is a new version, never an edit to an old one, so a
snapshot always says what it counted.

No metric ranks people (INT-11): figures break down by year, funder, activity, topic, phase,
or group, never by person.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from tessera.core.errors import TesseraError

if TYPE_CHECKING:
    from tessera.insight.figure import Figure, FigureRequest
    from tessera.insight.inputs import Inputs


class MetricError(TesseraError):
    """A metric or option that does not exist."""


@dataclass(frozen=True)
class Option:
    """A choice a figure offers, with the values it accepts."""

    key: str
    label: str
    choices: tuple[tuple[str, str], ...]
    default: str
    free: bool = False  # any short key is accepted (checked when the figure is computed)

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "default": self.default,
            "choices": [{"value": v, "label": label} for v, label in self.choices],
            "free": self.free,
        }

    def label_of(self, value: str) -> str:
        return dict(self.choices).get(value, value)


BASIS = Option(
    "basis",
    "Papers and studies",
    (("likely", "Confirmed and likely"), ("confirmed", "Confirmed only")),
    "likely",
)

PAPER_RULE = (
    "A paper counts for a member when their authorship is confirmed or, unless confirmed only "
    "is chosen, is a strong suggestion not yet decided; rejected and weaker suggestions never "
    "count. Each paper counts once, however many members wrote it (distinct PubMed IDs), in "
    "its year of publication. Retracted papers are not counted."
)
WINDOW_RULE = (
    "For a group, a member's work counts from the year they joined to the year they left, "
    "where those dates are known."
)


@dataclass(frozen=True)
class Metric:
    id: str
    version: int
    name: str
    area: str  # where it sits in Insight: Publications, Funding, Topics, Studies
    definition: str  # what it is, in a sentence or two
    counting: str  # exactly what is counted, and how
    unit: str  # papers, grants, dollars, studies
    sources: tuple[str, ...]  # connector names behind it
    period: str = "calendar"  # calendar years, the funder's fiscal years, or "upcoming"
    options: tuple[Option, ...] = ()
    caveats: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()  # the input tables it is computed from (see inputs.py)
    compute: Callable[[Inputs, FigureRequest], Figure] | None = field(
        default=None, compare=False, repr=False
    )

    @property
    def ref(self) -> str:
        return f"{self.id}/v{self.version}"

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "ref": self.ref,
            "name": self.name,
            "area": self.area,
            "definition": self.definition,
            "counting": self.counting,
            "unit": self.unit,
            "sources": list(self.sources),
            "period": self.period,
            "options": [o.to_json() for o in self.options],
            "caveats": list(self.caveats),
        }

    def resolve_options(self, given: Mapping[str, str | None]) -> dict[str, str]:
        """The figure's options: what was asked for, checked, with defaults for the rest."""
        out: dict[str, str] = {}
        for option in self.options:
            value = given.get(option.key) or option.default
            if option.free:
                if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", value):
                    raise MetricError(f"{option.label}: {value!r} is not a key.")
            elif value not in dict(option.choices):
                allowed = ", ".join(v for v, _ in option.choices)
                raise MetricError(f"{option.label}: {value!r} is not one of {allowed}.")
            out[option.key] = value
        return out


REGISTRY: dict[str, Metric] = {}
AREAS = ("Publications", "Funding", "Topics", "Studies")


def register(metric: Metric) -> Metric:
    if metric.id in REGISTRY:
        raise ValueError(f"metric {metric.id} is defined twice")
    if metric.compute is None:
        raise ValueError(f"metric {metric.id} has no compute function")
    REGISTRY[metric.id] = metric
    return metric


def _load() -> None:
    """Import the modules that define metrics (they register themselves)."""
    from tessera.insight import funding, publications  # noqa: F401


def get(metric_id: str) -> Metric:
    _load()
    found = REGISTRY.get(metric_id)
    if found is None:
        raise MetricError(f"There is no metric {metric_id!r}.")
    return found


def all_metrics() -> list[Metric]:
    _load()
    order = {area: i for i, area in enumerate(AREAS)}
    return sorted(REGISTRY.values(), key=lambda m: (order.get(m.area, 99), m.id))


__all__ = [
    "AREAS",
    "BASIS",
    "PAPER_RULE",
    "REGISTRY",
    "WINDOW_RULE",
    "Metric",
    "MetricError",
    "Option",
    "all_metrics",
    "get",
    "register",
]
