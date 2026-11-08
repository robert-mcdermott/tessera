"""Rebuild derived artifacts, recorded as a run."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.engine import Engine

from tessera.config import Config
from tessera.home import DataHome
from tessera.jobs import runs
from tessera.jobs.lock import job_lock
from tessera.search.index import build_index


def build_discovery(config: Config, home: DataHome, engine: Engine) -> dict[str, Any]:
    """Concept profiles, similar people, and the collaboration graph (``derived/``)."""
    from tessera.analytics.concepts import build_profiles
    from tessera.analytics.graph import build_graph

    home.require()
    with job_lock(home.root):
        run_id = runs.start_run(engine, "discovery", "build", {"artifact": "discovery"})
        try:
            result = build_profiles(config, home, engine)
            result |= build_graph(config, home, engine)
        except BaseException as exc:
            runs.finish_run(engine, run_id, "failed", {}, f"{exc.__class__.__name__}: {exc}")
            raise
        runs.finish_run(engine, run_id, "succeeded", result)
        return {"run_id": run_id, **result}


def build_attribution(
    config: Config, home: DataHome, engine: Engine, today: date | None = None
) -> dict[str, Any]:
    """Suggest which papers are each group's own (``attributed_to``), after the profiles."""
    from tessera.groups.attribution import suggest

    home.require()
    with job_lock(home.root):
        run_id = runs.start_run(engine, "groups", "attribution", {})
        try:
            result = suggest(config, home, engine, today=today)
        except BaseException as exc:
            runs.finish_run(engine, run_id, "failed", {}, f"{exc.__class__.__name__}: {exc}")
            raise
        runs.finish_run(engine, run_id, "succeeded", result)
        return {"run_id": run_id, **result}


def build_search(config: Config, home: DataHome, engine: Engine) -> dict[str, Any]:
    home.require()
    with job_lock(home.root):
        run_id = runs.start_run(engine, "search", "build", {"artifact": "search"})
        try:
            result = build_index(config, home, engine)
        except BaseException as exc:
            runs.finish_run(engine, run_id, "failed", {}, f"{exc.__class__.__name__}: {exc}")
            raise
        runs.finish_run(engine, run_id, "succeeded", result)
        return {"run_id": run_id, **result}
