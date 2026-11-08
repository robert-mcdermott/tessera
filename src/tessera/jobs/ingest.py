"""Run a connector: fetch, keep raw payloads, parse, write the lake, record the run.

The flow for every source::

    plan  <- connector.plan(options, checkpoint)
    for payload in connector.fetch(plan):
        raw zone  <- payload            (before parsing, so it can be replayed)
        lake      <- connector.parse(payload)
    ledger <- run record; checkpoint advanced only if the run was complete

``replay`` feeds the saved payloads of an earlier run through the same parser.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx
from sqlalchemy.engine import Engine

from tessera.config import Config
from tessera.connectors import CONNECTORS
from tessera.connectors.base import Connector, FetchReport, IngestOptions, Payload, Plan
from tessera.connectors.http import SourceError
from tessera.connectors.raw import RawStore, iter_payloads
from tessera.core.errors import TesseraError
from tessera.core.ids import new_id
from tessera.home import DataHome
from tessera.jobs import runs
from tessera.jobs.lock import job_lock
from tessera.lake import Lake, LakeWriter
from tessera.lake.schemas import DATASETS

log = logging.getLogger("tessera.ingest")

PUBLISH_EVERY = 25  # payloads between lake publishes, so interruptions lose little
ICITE_REFRESH_DAYS = 30
INGEST_ORDER = ("reporter", "ctgov", "pubmed", "pubmed:linked", "icite")


@dataclass
class RunResult:
    run_id: str
    source: str
    status: str  # succeeded | partial | failed
    plan: Plan | None
    report: FetchReport
    counts: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class _Writers:
    """Lake writers per dataset, opened on first use."""

    def __init__(self, home: DataHome, run_id: str, report: FetchReport) -> None:
        self.home, self.run_id, self.report = home, run_id, report
        self.open: dict[str, LakeWriter] = {}

    def add(self, records: dict[str, list[dict[str, Any]]], payload: Payload) -> None:
        for dataset, rows in records.items():
            if not rows:
                continue
            writer = self.open.get(dataset)
            if writer is None:
                writer = self.open[dataset] = LakeWriter(
                    self.home.lake,
                    DATASETS[dataset],
                    self.run_id,
                    source_version=payload.source_version,
                )
            writer.add(rows, retrieved_at=payload.retrieved_at)

    def publish(self) -> None:
        for writer in self.open.values():
            writer.publish()

    def commit(self) -> dict[str, Any]:
        stats: dict[str, Any] = {}
        for dataset, writer in self.open.items():
            result = writer.commit()
            stats[dataset] = {
                "received": result.received,
                "unchanged": result.unchanged,
                "written": result.written,
            }
        return stats

    def abort(self) -> None:
        for writer in self.open.values():
            writer.abort()


def _source_and_mode(source: str, options: IngestOptions) -> tuple[str, IngestOptions]:
    if ":" in source:  # e.g. "pubmed:linked"
        source, mode = source.split(":", 1)
        options = IngestOptions(**{**options.__dict__, "mode": mode})
    if source not in CONNECTORS:
        raise TesseraError(f"Unknown source {source!r}; choose from {', '.join(CONNECTORS)}")
    return source, options


def _resolve_ids(
    home: DataHome, engine: Engine, source: str, options: IngestOptions
) -> IngestOptions:
    """Fill in IDs (or name terms) for modes that derive them from the lake or ledger."""
    if source == "pubmed" and options.mode == "people" and not options.terms:
        return IngestOptions(**{**options.__dict__, "terms": people_terms(engine)})
    if options.ids:
        return options
    if source == "icite" and options.mode in {"auto", "refresh"}:
        ids = pmids_needing_metrics(home)
    elif source == "pubmed" and options.mode == "linked":
        ids = linked_pmids_missing(home)
    else:
        return options
    return IngestOptions(**{**options.__dict__, "ids": ids})


def pmids_needing_metrics(home: DataHome, refresh_days: int = ICITE_REFRESH_DAYS) -> list[str]:
    with Lake(home.lake) as lake:
        rows = lake.rows(
            "SELECT a.pmid FROM pubmed_article a LEFT JOIN icite_metrics m ON m.pmid = a.pmid "
            "WHERE m.pmid IS NULL OR m._retrieved_at < now() - to_days(CAST(? AS INTEGER)) "
            "ORDER BY a.pmid",
            [refresh_days],
        )
    return [r["pmid"] for r in rows]


def people_terms(engine: Engine) -> list[tuple[str, str]]:
    """PubMed name queries for every active person, in a stable order."""
    from tessera.connectors.pubmed import person_terms
    from tessera.evidence.store import load_people

    terms = []
    for record in sorted(load_people(engine, ("active",)), key=lambda r: r.id):
        found = person_terms([(n.family, n.given, n.initials) for n in record.names])
        if found:
            terms.append(found)
    return terms


def linked_pmids_missing(home: DataHome) -> list[str]:
    """PMIDs that grants or studies point to but that the lake doesn't have yet."""
    with Lake(home.lake) as lake:
        rows = lake.rows(
            "SELECT pmid FROM (SELECT pmid FROM reporter_publication_link "
            "UNION SELECT pmid FROM ctgov_reference) l "
            "WHERE NOT EXISTS (SELECT 1 FROM pubmed_article a WHERE a.pmid = l.pmid) ORDER BY pmid"
        )
    return [r["pmid"] for r in rows]


def ingest(
    config: Config,
    home: DataHome,
    engine: Engine,
    source: str,
    options: IngestOptions | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] | None = None,
    today: date | None = None,
    publish_every: int = PUBLISH_EVERY,
) -> RunResult:
    """Fetch one source into the lake. Holds the job lock for the duration."""
    home.require()
    source, options = _source_and_mode(source, options or IngestOptions())
    connector_cls = CONNECTORS[source]
    if not getattr(config.sources, source).enabled:
        raise TesseraError(f"{connector_cls.label} is disabled in the configuration.")
    with job_lock(home.root):
        interrupted = runs.mark_interrupted(engine)
        if interrupted:
            log.warning("marked %d interrupted run(s) as failed", interrupted)
        options = _resolve_ids(home, engine, source, options)
        http_kwargs: dict[str, Any] = {}
        if transport is not None:
            http_kwargs["transport"] = transport
        if sleep is not None:
            http_kwargs["sleep"] = sleep
        http = connector_cls.make_http(config, **http_kwargs)
        try:
            connector = connector_cls(http, config)
            return _run(
                connector, config, home, engine, options, today or date.today(), publish_every
            )
        finally:
            http.close()


def _run(
    connector: Connector,
    config: Config,
    home: DataHome,
    engine: Engine,
    options: IngestOptions,
    today: date,
    publish_every: int,
) -> RunResult:
    source = connector.name
    report = FetchReport()
    run_id = new_id("run")
    plan: Plan | None = None
    try:
        scope_probe = connector.plan(options, None, today)
        checkpoint = runs.get_checkpoint(engine, source, scope_probe.scope)
        plan = connector.plan(options, checkpoint, today)
    except TesseraError as exc:
        runs.start_run(engine, source, "fetch", {"mode": options.mode}, run_id)
        runs.finish_run(engine, run_id, "failed", {}, str(exc))
        raise
    params = {
        "mode": plan.mode,
        "scope": plan.scope,
        "description": plan.description,
        "checkpoint": checkpoint,
    }
    runs.start_run(engine, source, "fetch", params, run_id)
    log.info("%s run %s: %s", connector.label, run_id, plan.description)

    if plan.empty:
        runs.finish_run(engine, run_id, "succeeded", {"report": report.to_json(), "lake": {}})
        log.info("%s: nothing to fetch", connector.label)
        return RunResult(run_id, source, "succeeded", plan, report)

    raw = RawStore(home.raw, source, run_id)
    writers = _Writers(home, run_id, report)
    error: str | None = None
    try:
        for count, payload in enumerate(connector.fetch(plan, report), 1):
            payload.source_version = report.data_version
            raw.save(payload)
            writers.add(connector.parse(payload), payload)
            if count % publish_every == 0:
                writers.publish()
                log.info("%s: %d payloads so far", connector.label, count)
    except (SourceError, TesseraError) as exc:
        error = str(exc)
        log.error("%s run %s stopped: %s", connector.label, run_id, exc)
    except BaseException as exc:  # including KeyboardInterrupt: keep what was fetched
        error = f"{exc.__class__.__name__}: {exc}"
        lake_stats = writers.commit()
        runs.finish_run(
            engine, run_id, "failed", {"report": report.to_json(), "lake": lake_stats}, error
        )
        raise
    lake_stats = writers.commit()

    if error:
        status = "failed"
    elif report.complete:
        status = "succeeded"
    else:
        status = "partial"
    if status == "succeeded" and plan.scope and plan.new_watermark:
        runs.set_checkpoint(engine, source, plan.scope, plan.new_watermark, run_id)
    counts = {"report": report.to_json(), "lake": lake_stats}
    runs.finish_run(engine, run_id, status, counts, error)
    log.info("%s run %s %s", connector.label, run_id, status)
    return RunResult(run_id, source, status, plan, report, counts, error)


def ingest_many(
    config: Config,
    home: DataHome,
    engine: Engine,
    sources: Iterable[str] = INGEST_ORDER,
    **kwargs: Any,
) -> list[RunResult]:
    """Run several sources in order, skipping disabled ones and continuing past failures."""
    results = []
    for name in sources:
        base = name.split(":", 1)[0]
        if not getattr(config.sources, base).enabled:
            continue
        try:
            results.append(ingest(config, home, engine, name, **kwargs))
        except TesseraError as exc:
            log.error("%s: %s", name, exc)
            results.append(RunResult("", base, "failed", None, FetchReport(), error=str(exc)))
    return results


def replay(config: Config, home: DataHome, engine: Engine, source: str, of_run: str) -> RunResult:
    """Rebuild lake records from an earlier run's raw payloads, without the network."""
    home.require()
    if source not in CONNECTORS:
        raise TesseraError(f"Unknown source {source!r}; choose from {', '.join(CONNECTORS)}")
    connector_cls = CONNECTORS[source]
    with job_lock(home.root):
        run_id = new_id("run")
        runs.start_run(engine, source, "replay", {"of": of_run}, run_id)
        report = FetchReport()
        writers = _Writers(home, run_id, report)
        try:
            for payload in iter_payloads(home.raw, source, of_run):
                report.payloads += 1
                writers.add(connector_cls.parse(payload), payload)
        except Exception as exc:
            writers.abort()
            runs.finish_run(engine, run_id, "failed", {}, str(exc))
            raise
        counts = {"report": report.to_json(), "lake": writers.commit()}
        runs.finish_run(engine, run_id, "succeeded", counts)
        return RunResult(run_id, source, "succeeded", None, report, counts)


def expected_days(source: str) -> int:
    """How stale a source may get before the UI warns (days)."""
    return {"pubmed": 7, "reporter": 7, "ctgov": 7, "icite": 45}.get(source, 7)


__all__ = ["RunResult", "expected_days", "ingest", "ingest_many", "replay"]
