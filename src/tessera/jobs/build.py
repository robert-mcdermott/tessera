"""Rebuild derived artifacts, recorded as a run."""

from __future__ import annotations

from typing import Any

from sqlalchemy.engine import Engine

from tessera.config import Config
from tessera.home import DataHome
from tessera.jobs import runs
from tessera.jobs.lock import job_lock
from tessera.search.index import build_index


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
