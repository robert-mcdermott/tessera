"""Make a data home with an earlier release, for ``check_upgrades.py``.

Run by that release's own Python, from a copy of that release (its ``tests`` package beside it):

    <release>/.venv/bin/python upgrade_seed.py <release dir> <org dir>

Releases with ``tessera demo`` (0.3.0 on) build the demonstration organization; earlier ones
build the synthetic organization their own tests use, resolve it, and make a few decisions
through their API, as a curator would. Either way the result is an installation with people,
links, decisions, and a search index, made entirely by that release.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import date
from pathlib import Path

TODAY = date(2026, 11, 7)


def main() -> int:
    release, org = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    sys.path.insert(0, str(release))  # that release's tests package
    tessera = Path(sys.executable).parent / "tessera"
    commands = subprocess.run([str(tessera), "--help"], capture_output=True, text=True).stdout
    listed = re.search(r"^\s+demo\s", commands, re.MULTILINE) or ",demo," in commands
    if listed:
        subprocess.run([str(tessera), "demo", str(org)], check=True, stdout=subprocess.DEVNULL)
        return 0
    return _synthetic(org)


def _synthetic(org: Path) -> int:
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from tests import synthetic_org  # type: ignore[import-not-found]

    from tessera.api.app import create_app
    from tessera.config import example_config_text, load_config
    from tessera.evidence.people import import_roster
    from tessera.evidence.resolve import resolve
    from tessera.home import DataHome
    from tessera.jobs.build import build_search
    from tessera.ledger import migrate
    from tessera.ledger.db import make_engine
    from tessera.ledger.tables import assertion, person

    org.mkdir(parents=True, exist_ok=True)
    (org / "tessera.toml").write_text(example_config_text())
    config = load_config(org / "tessera.toml", env={})
    home = DataHome(config.data.home)
    home.create()
    engine = make_engine(config.ledger_url)
    migrate.upgrade(engine)
    world = synthetic_org.build(org, newcomers=3)
    synthetic_org.load(world, home.lake)
    import_roster(engine, world.roster_path)
    resolve(config, home, engine, today=TODAY)
    build_search(config, home, engine)
    with engine.connect() as conn:
        suggested = (
            conn.execute(
                select(assertion.c.id)
                .where(assertion.c.status == "suggested")
                .order_by(assertion.c.id)
            )
            .scalars()
            .all()
        )
        candidates = (
            conn.execute(
                select(person.c.id).where(person.c.status == "candidate").order_by(person.c.id)
            )
            .scalars()
            .all()
        )
    engine.dispose()
    with TestClient(create_app(config), base_url="http://127.0.0.1") as client:
        for i, aid in enumerate(suggested[:12]):  # a curator's first session
            action = ("accept", "reject", "defer")[i % 3]
            answer = client.post(f"/api/v1/assertions/{aid}/decision", json={"action": action,
                                 "note": f"seeded {action}"})  # fmt: skip
            assert answer.status_code == 200, answer.text
        if candidates:
            answer = client.post(
                "/api/v1/candidates/decisions", json={"ids": candidates[:1], "action": "promote"}
            )
            assert answer.status_code == 200, answer.text
    return 0


if __name__ == "__main__":
    sys.exit(main())
