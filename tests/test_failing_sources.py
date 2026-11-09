"""A sync meets failing sources (wave 8, slice 4's drills): one down, one answering with server
errors, one asking to wait, one that never answers, and one sending something that is not what
it should be. Each failure is recorded against its own source with what went wrong; the other
sources, the resolve, and the builds go on; the last good data stays; and the next sync, with
the sources well again, catches up."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date

import httpx
import pytest
from sqlalchemy import select

from tessera.connectors.http import HttpClient
from tessera.core.clock import utc_now
from tessera.jobs.sync import sync
from tessera.lake import Lake
from tessera.ledger.db import make_engine
from tessera.ledger.tables import ingest_run, source_checkpoint

from .conftest import Instance
from .fakes import FakeApi, no_sleep
from .test_ingest import world

TODAY = date(2026, 11, 7)


@pytest.fixture
def network(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    api = world()
    original = HttpClient.__init__

    def init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", api.transport())
        kwargs.setdefault("sleep", no_sleep)
        original(self, *args, **kwargs)

    monkeypatch.setattr(HttpClient, "__init__", init)
    return api


@pytest.fixture
def engine(instance: Instance) -> Iterator:  # type: ignore[type-arg]
    engine = make_engine(instance.config.ledger_url)
    yield engine
    engine.dispose()


def down(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("Connection refused", request=request)


def silent(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


def server_error(request: httpx.Request) -> httpx.Response:
    return httpx.Response(503, text="<html><body>Service Unavailable</body></html>")


def garbled(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="<html><body>Our systems are under maintenance</body></html>")


def fails_like(handler: Callable[[httpx.Request], httpx.Response], api: FakeApi,
               *paths: str) -> dict[str, Callable]:  # fmt: skip
    """Make these routes fail; return what they were, to restore."""
    saved = {p: api.routes[p] for p in paths}
    for p in paths:
        api.routes[p] = handler
    return saved


def statuses(result) -> dict[str, str]:  # type: ignore[no-untyped-def]
    out: dict[str, str] = {}
    for run in result.ingests:
        out.setdefault(run.source, run.status)  # a source's first run in the sync
    return out


def lake_counts(instance: Instance) -> dict[str, int]:
    with Lake(instance.home.lake) as lake:
        return {t: lake.rows(f"SELECT count(*) AS n FROM {t}")[0]["n"]
                for t in ("pubmed_article", "reporter_project", "ctgov_study")}  # fmt: skip


@pytest.mark.parametrize(
    ("handler", "paths", "source", "says"),
    [
        (down, ("projects/search", "publications/search"), "reporter", "ConnectError"),
        (server_error, ("studies",), "ctgov", "HTTP 503"),
        (silent, ("pubs",), "icite", "ReadTimeout"),
        (garbled, ("efetch.fcgi",), "pubmed", "not PubMed's articles"),
        (garbled, ("projects/search", "publications/search"), "reporter", ""),
        (garbled, ("studies",), "ctgov", ""),
    ],
    ids=["down", "server errors", "never answers", "garbled XML", "garbled JSON", "garbled JSON 2"],
)
def test_a_failing_source_leaves_the_rest_of_the_sync_to_run(
    instance: Instance,
    engine,
    network: FakeApi,
    handler,
    paths,
    source,
    says,  # type: ignore[no-untyped-def]
) -> None:
    first = sync(instance.config, instance.home, engine, today=TODAY)
    assert first.status == "succeeded", statuses(first)
    kept = lake_counts(instance)
    with engine.connect() as conn:
        checkpoints = {r.source: r.watermark for r in conn.execute(select(source_checkpoint))}

    saved = fails_like(handler, network, *paths)
    started = utc_now()
    second = sync(instance.config, instance.home, engine, today=TODAY)
    assert second.status == "partial"
    # Failed, or partial for a source that reads in batches and goes on past one that fails;
    # either way it says what went wrong.
    assert statuses(second)[source] in ("failed", "partial")
    others = {s: st for s, st in statuses(second).items() if s != source}
    assert set(others.values()) <= {"succeeded", "partial"}, others
    failed = next(r for r in second.ingests if r.source == source and r.status != "succeeded")
    told = failed.error or str(failed.report.to_json())
    assert says in told, told
    assert {r.status for r in second.resolves} == {"succeeded"}  # resolved anyway
    assert second.build, "the builds ran"
    assert lake_counts(instance) == kept  # the last good data stays
    with engine.connect() as conn:
        recorded = (
            conn.execute(
                select(ingest_run.c.status).where(
                    ingest_run.c.source == source, ingest_run.c.started_at >= started
                )
            )
            .scalars()
            .all()
        )
        assert set(recorded) & {"failed", "partial"}, recorded
        now = {r.source: r.watermark for r in conn.execute(select(source_checkpoint))}
    assert now.get(source) == checkpoints.get(source)  # not advanced past what failed

    network.routes.update(saved)  # well again
    third = sync(instance.config, instance.home, engine, today=TODAY)
    assert third.status == "succeeded", statuses(third)


def test_a_source_that_asks_to_wait_is_waited_for(
    instance: Instance,
    engine,
    network: FakeApi,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    waited: list[float] = []
    original = HttpClient.__init__

    def init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["sleep"] = waited.append
        original(self, *args, **kwargs)

    monkeypatch.setattr(HttpClient, "__init__", init)
    studies = network.routes["studies"]
    asked = iter([True])

    def busy(request: httpx.Request) -> httpx.Response:
        if next(asked, False):
            return httpx.Response(429, headers={"Retry-After": "7"}, text="slow down")
        return studies(request)

    network.routes["studies"] = busy
    result = sync(instance.config, instance.home, engine, today=TODAY)
    assert statuses(result)["ctgov"] == "succeeded"
    assert 7 in waited


def test_keys_never_reach_the_logs_the_raw_responses_or_the_ledger(
    instance: Instance,
    engine,
    network: FakeApi,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    """NCBI's API key goes in each request; it must not be written anywhere Tessera keeps
    things, even with every message logged (wave 8)."""
    import logging

    from tessera.core.log import configure_logging

    secret = "NCBIKEY-0123456789abcdef"
    monkeypatch.setenv(instance.config.sources.pubmed.api_key_env or "NCBI_API_KEY", secret)
    logs = instance.home.root / "logs"
    configure_logging(verbosity=1, log_dir=logs)
    logging.getLogger("httpx").setLevel(logging.DEBUG)  # as a curious admin might
    try:
        result = sync(instance.config, instance.home, engine, today=TODAY)
    finally:
        configure_logging()
    assert result.status == "succeeded"
    assert any(secret in str(r.url) + r.content.decode("utf-8", "replace")
               for r in network.requests), "the key was sent"  # fmt: skip
    kept = [p for p in instance.home.root.rglob("*") if p.is_file()]
    assert kept and not [p for p in kept if secret.encode() in p.read_bytes()], "found the key"
    with engine.connect() as conn:
        runs_text = str(conn.execute(select(ingest_run)).all())
    assert secret not in runs_text
