"""Many people at once (PLT-16): one build of an expensive result, sign-in limits that hold
across server processes, branding that reaches every process, and several processes."""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from tessera.bench.load import LoadRun
from tessera.cli import EXIT_OK, main
from tessera.core.clock import utc_now
from tessera.core.once import Once
from tessera.ledger.db import make_engine
from tessera.ledger.tables import failed_sign_in

from .conftest import Instance


def test_requests_at_the_same_moment_share_one_build() -> None:
    once, builds, results = Once(), [], []
    gate = threading.Event()

    def build() -> str:
        builds.append(1)
        gate.wait(5)
        return "the figure"

    def ask() -> None:
        results.append(once.run(("figure", 1), build))

    threads = [threading.Thread(target=ask) for _ in range(12)]
    for t in threads:
        t.start()
    for _ in range(100):  # every request is waiting on the one build
        if once.building() == 1 and len(builds) == 1:
            break
        time.sleep(0.01)
    time.sleep(0.05)
    gate.set()
    for t in threads:
        t.join()
    assert len(builds) == 1 and results == ["the figure"] * 12
    assert once.building() == 0
    assert once.run(("figure", 1), lambda: "built again") == "built again"  # nothing kept


def test_a_failed_build_reaches_those_waiting_and_is_not_kept() -> None:
    once, gate, errors = Once(), threading.Event(), []

    def fail() -> str:
        gate.wait(5)
        raise RuntimeError("the lake is not there")

    def ask() -> None:
        try:
            once.run("key", fail)
        except RuntimeError as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=ask) for _ in range(4)]
    for t in threads:
        t.start()
    time.sleep(0.1)
    gate.set()
    for t in threads:
        t.join()
    assert errors == ["the lake is not there"] * 4
    assert once.run("key", lambda: "fine now") == "fine now"


def test_the_sign_in_limit_is_kept_in_the_ledger_by_hash(instance: Instance) -> None:
    from tessera.auth.sessions import SignInThrottle

    now = [utc_now()]
    one = SignInThrottle(limit=3, window=60, clock=lambda: now[0])
    other = SignInThrottle(limit=3, window=60, clock=lambda: now[0])  # another process
    engine = make_engine(instance.config.ledger_url)
    try:
        keys = ("user:someone", "addr:203.0.113.9")
        for _ in range(2):
            one.failed(engine, *keys)
        assert not other.blocked(engine, *keys)
        other.failed(engine, *keys)
        assert one.blocked(engine, *keys) and other.blocked(engine, "user:someone")
        assert not one.blocked(engine, "user:someone-else")
        with engine.connect() as conn:
            stored = conn.execute(select(failed_sign_in.c.key_hash)).scalars().all()
        assert len(stored) == 6 and not any("someone" in h or "203.0" in h for h in stored)
        now[0] += timedelta(seconds=61)  # the window passes
        assert not one.blocked(engine, *keys)
        one.failed(engine, "user:another")  # and old failures are cleared as new ones come
        with engine.connect() as conn:
            assert len(conn.execute(select(failed_sign_in.c.id)).all()) == 1
        one.failed(engine, *keys)
        one.succeeded(engine, *keys)
        assert not one.blocked(engine, *keys)
    finally:
        engine.dispose()


def test_branding_saved_in_one_process_reaches_the_others(instance: Instance) -> None:
    from dataclasses import replace

    from tessera import brandkit
    from tessera.api.app import create_app
    from tessera.api.common import follow_branding

    saving, other = create_app(instance.config), create_app(instance.config)
    assert other.state.config.branding.product_name == instance.config.branding.product_name
    proposal = replace(brandkit.current(instance.config), product_name="Mosaic at Corvane")
    brandkit.save(instance.config, proposal)
    other.state.branding_checked = 0.0  # it looks no more than once a second
    follow_branding(other)
    assert other.state.config.branding.product_name == "Mosaic at Corvane"
    assert other.state.config.server == instance.config.server  # nothing else changes
    follow_branding(other)  # nothing new: nothing to do
    assert other.state.config.branding.product_name == "Mosaic at Corvane"
    del saving


def test_serve_runs_the_processes_asked_for(
    instance: Instance, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import uvicorn

    seen: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: seen.update(kwargs, app=app))
    assert main(["--config", str(instance.path), "serve", "--port", "18766", "--workers", "3"]) == 0
    assert seen["workers"] == 3 and seen["factory"] is True
    assert seen["timeout_worker_healthcheck"] >= 30  # busy is not hung
    assert "in 3 processes" in capsys.readouterr().out
    monkeypatch.setenv("TESSERA_SERVER_WORKERS", "2")  # as the service files set it
    assert main(["--config", str(instance.path), "serve", "--port", "18766"]) == EXIT_OK
    assert seen["workers"] == 2
    monkeypatch.delenv("TESSERA_SERVER_WORKERS")
    assert main(["--config", str(instance.path), "serve", "--port", "18766"]) == EXIT_OK
    assert seen["workers"] is None  # one process, uvicorn's own way
    assert main(["--config", str(instance.path), "serve", "--workers", "0"]) != EXIT_OK


def test_doctor_asks_a_server_for_several_processes(
    instance: Instance, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--config", str(instance.path), "doctor", "--production"])
    assert "! Production: processes" in capsys.readouterr().out
    monkeypatch.setenv("TESSERA_SERVER_WORKERS", "4")
    main(["--config", str(instance.path), "doctor", "--production"])
    assert "✓ Production: processes" in capsys.readouterr().out


def test_workers_must_be_sensible(tmp_path: Path) -> None:
    from tessera.config import ConfigError, example_config_text, load_config

    path = tmp_path / "tessera.toml"
    path.write_text(example_config_text().replace("workers = 1", "workers = 0"))
    with pytest.raises(ConfigError, match="workers"):
        load_config(path, env={})


def test_a_load_run_with_nothing_measured_is_neither_within_nor_over() -> None:
    empty = LoadRun("realistic", 2, 1, 1.0, 0, 0.0, 0, 0.0, 0.0, 0.0)
    assert empty.within is None
    busy = LoadRun("without pause", 2, 1, 1.0, 10, 10.0, 0, 50.0, 900.0, 4000.0)
    assert busy.within is True
    assert LoadRun("without pause", 2, 1, 1.0, 10, 10.0, 1, 50.0, 90.0, 400.0).within is False
