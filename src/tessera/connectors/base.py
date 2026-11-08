"""Shared types for connectors.

A connector has two halves, kept deliberately separate:

* ``fetch(plan, report)`` talks to the network and yields :class:`Payload` objects:
  the response bodies that carry records.
* ``parse(payload)`` is a pure function from one payload to lake records, grouped by
  dataset.

Ingestion saves every payload to the raw zone before parsing it, and replay feeds saved
payloads through the same ``parse``. Re-parsing never needs the network.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, ClassVar

from tessera.config import Config
from tessera.connectors.http import HttpClient

log = logging.getLogger("tessera.connectors")

Records = dict[str, list[dict[str, Any]]]  # dataset name -> lake records


@dataclass
class Payload:
    kind: str  # what the body contains, e.g. "efetch", "projects", "links"
    content: bytes
    media_type: str  # "json" or "xml"
    retrieved_at: datetime
    request: dict[str, Any] = field(default_factory=dict)  # a summary, for the raw index
    source_version: str | None = None  # the source's data version when this was fetched

    def json(self) -> Any:
        return json.loads(self.content)


@dataclass
class Plan:
    """What one ingest run will fetch."""

    mode: str
    scope: str  # checkpoint key; empty when the run does not use a checkpoint
    description: str
    new_watermark: str | None = None  # saved as the checkpoint if the run completes
    params: dict[str, Any] = field(default_factory=dict)
    empty: bool = False  # nothing to fetch (e.g. no linked PMIDs are missing)


@dataclass
class IngestOptions:
    mode: str = "auto"  # auto | org | ids | query
    ids: list[str] = field(default_factory=list)
    query: str | None = None
    since: date | None = None
    until: date | None = None
    full: bool = False  # ignore the checkpoint and fetch the whole window
    max_results: int | None = None  # cap for quick checks; never advances the checkpoint
    terms: list[tuple[str, str]] = field(default_factory=list)  # PubMed people mode
    known: dict[str, Any] = field(default_factory=dict)  # what the lake holds, to fetch changes


@dataclass
class FetchReport:
    """What happened while fetching. A run is complete only with no failures or truncation."""

    total_found: int | None = None
    expected: int | None = None
    payloads: int = 0
    truncated: bool = False
    failures: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    data_version: str | None = None
    found_ids: list[str] = field(default_factory=list)  # e.g. linked IDs worth fetching later

    @property
    def complete(self) -> bool:
        return not self.failures and not self.truncated

    def fail(self, where: dict[str, Any], error: Exception | str) -> None:
        self.failures.append({**where, "error": str(error)})
        log.warning("batch %s failed: %s", where, error)

    def note(self, message: str) -> None:
        self.notes.append(message)
        log.warning("%s", message)

    def to_json(self) -> dict[str, Any]:
        return {
            "total_found": self.total_found,
            "expected": self.expected,
            "payloads": self.payloads,
            "truncated": self.truncated,
            "failures": self.failures[:50],
            "failure_count": len(self.failures),
            "notes": self.notes,
            "data_version": self.data_version,
        }


class Connector(ABC):
    name: ClassVar[str]
    label: ClassVar[str]
    datasets: ClassVar[tuple[str, ...]]
    base_url: ClassVar[str]
    min_interval: ClassVar[float]
    modes: ClassVar[tuple[str, ...]]

    def __init__(self, http: HttpClient, config: Config) -> None:
        self.http = http
        self.config = config

    @classmethod
    def make_http(cls, config: Config, **kwargs: Any) -> HttpClient:
        return HttpClient(
            cls.name,
            cls.base_url,
            min_interval=kwargs.pop("min_interval", cls.min_interval),
            contact_email=config.sources.contact_email,
            **kwargs,
        )

    @abstractmethod
    def plan(self, options: IngestOptions, checkpoint: str | None, today: date) -> Plan:
        """Decide what to fetch from the options, the saved checkpoint, and today's date."""

    @abstractmethod
    def fetch(self, plan: Plan, report: FetchReport) -> Iterator[Payload]:
        """Yield record-bearing payloads. Recoverable batch failures go into ``report``."""

    @classmethod
    @abstractmethod
    def parse(cls, payload: Payload) -> Records:
        """Turn one payload into lake records (pure; no network)."""
