"""Upgrade an installation made by every earlier release to this one, and check nothing is lost
(PLT-18).

    uv run python scripts/check_upgrades.py                    # every release, on SQLite
    uv run python scripts/check_upgrades.py v0.1.0 v0.6.0      # some of them
    uv run python scripts/check_upgrades.py --postgres URL     # 0.7.0's, moved to PostgreSQL

For each release: a copy of it from its tag, installed with ``uv sync --frozen`` in its own
environment; a data home made by it (``upgrade_seed.py``); a backup taken by it, where it has
``tessera backup``; then, with this release, ``tessera db upgrade`` and ``tessera build``, and
the checks:

- every ledger row the release wrote is still there, with every column it had unchanged,
  except what a migration is known to change (``intended`` and ``moved``, each checked);
- the lake and the raw responses are untouched, file by file;
- ``tessera doctor`` passes, and the app's main pages answer;
- the release's backup restores into a new data home with this release, and holds the same rows.

It needs the network the first time a release's dependencies are installed, and git tags.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, create_engine, select, text

REPO = Path(__file__).resolve().parents[1]
RELEASES = ("v0.1.0", "v0.2.0", "v0.3.0", "v0.4.0", "v0.5.0", "v0.6.0", "v0.7.0")
PYTHON = sys.executable
TESSERA = [PYTHON, "-m", "tessera"]

# The schema's own version, and rows the check itself rewrites.
SKIP = {"alembic_version"}
SKIP_ROWS = {("setting", '["operations.last_backup"]')}  # the backup taken for the check


def intended(table: str, column: str, row: dict[str, Any], now: dict[str, Any]) -> bool:
    """What a later migration changes on purpose, exactly as it should; anything else must
    come through every upgrade as it was written."""
    if table == "assertion" and row.get("predicate") == "member_of" and row.get("qualifiers") == {}:
        # 0007: memberships gain a role, so a person's two memberships of one group differ.
        if column == "qualifiers":
            return now["qualifiers"] == {"role": "member", "source": "roster"}
        if column == "qualifier_key":
            return now["qualifier_key"] == "member"
    return False


def moved(before: dict[str, Any], after: dict[str, Any], what: str) -> list[str]:
    """0007: a group's one parent, in ``unit.parent_id``, became a row of ``unit_parent``, so
    a group may sit under several. Each must have arrived."""
    problems = []
    for row in before.get("unit", {}).values():
        if row.get("parent_id"):
            key = json.dumps([row["id"], row["parent_id"]])
            if key not in after.get("unit_parent", {}):
                problems.append(f"{what}: unit {row['id']}'s parent {row['parent_id']} is lost")
    return problems


DROPPED = {("unit", "parent_id")}  # 0007, checked by moved()


def run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None,
        quiet: bool = True) -> subprocess.CompletedProcess[str]:  # fmt: skip
    done = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"Failed: {' '.join(cmd)}\n{done.stdout[-3000:]}\n{done.stderr[-3000:]}")
    if not quiet:
        print(done.stdout, end="")
    return done


def install(release: str, work: Path) -> Path:
    """The release, from its tag, in ``work/<release>`` with its own environment."""
    src = work / release
    if not (src / ".venv").is_dir():
        shutil.rmtree(src, ignore_errors=True)
        src.mkdir(parents=True)
        archive = subprocess.run(["git", "-C", str(REPO), "archive", release],
                                 capture_output=True, check=True).stdout  # fmt: skip
        subprocess.run(["tar", "-x", "-C", str(src)], input=archive, check=True)
        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
        run(["uv", "sync", "--frozen", "--extra", "postgres", "--project", str(src)], env=env)
    return src


def clean_env() -> dict[str, str]:
    """Without anything that would point a release at this checkout or another ledger."""
    return {k: v for k, v in os.environ.items()
            if not k.startswith("TESSERA_") and k not in ("VIRTUAL_ENV", "PYTHONPATH")}  # fmt: skip


def plain(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return hashlib.sha256(value).hexdigest()
    if isinstance(value, str) and value[:1] in "[{":
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def ledger_rows(url: str) -> dict[str, dict[str, dict[str, Any]]]:
    """Every row of every table, by its primary key."""
    engine = create_engine(url)
    try:
        meta = MetaData()
        meta.reflect(engine)
        out: dict[str, dict[str, dict[str, Any]]] = {}
        with engine.connect() as conn:
            for table in meta.sorted_tables:
                if table.name in SKIP:
                    continue
                key = [c.name for c in table.primary_key.columns] or [c.name for c in table.columns]
                rows: dict[str, dict[str, Any]] = {}
                for row in conn.execute(select(table)).mappings():
                    values = {k: plain(v) for k, v in row.items()}
                    rows[json.dumps([values[k] for k in key], default=str)] = values
                out[table.name] = rows
        return out
    finally:
        engine.dispose()


def digests(root: Path) -> dict[str, str]:
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def compare_ledgers(before: dict[str, Any], after: dict[str, Any], what: str) -> list[str]:
    problems = moved(before, after, what)
    for table, rows in before.items():
        now = after.get(table)
        if now is None:
            problems.append(f"{what}: table {table} is gone")
            continue
        for key, row in rows.items():
            if (table, key) in SKIP_ROWS:
                continue
            found = now.get(key)
            if found is None:
                problems.append(f"{what}: {table} row {key} is gone")
                continue
            for column, value in row.items():
                if (table, column) in DROPPED:
                    continue
                if column not in found:
                    problems.append(f"{what}: {table}.{column} is gone")
                    break
                if found[column] != value and not intended(table, column, row, found):
                    problems.append(
                        f"{what}: {table} row {key} column {column}: "
                        f"{str(value)[:80]!r} became {str(found[column])[:80]!r}"
                    )
    return problems


def pages(config_path: Path) -> list[str]:
    """The app's main pages, answered by this release from the upgraded installation."""
    from fastapi.testclient import TestClient

    from tessera.api.app import create_app
    from tessera.config import load_config

    problems = []
    config = load_config(config_path, env={})
    with TestClient(create_app(config), base_url="http://127.0.0.1") as client:

        def get(url: str) -> Any:
            answer = client.get(url)
            if answer.status_code != 200:
                problems.append(f"GET {url}: {answer.status_code} {answer.text[:200]}")
                return None
            return answer.json()

        people = get("/api/v1/people?limit=20")
        for url in ("/api/v1/stats", "/api/v1/publications?limit=20", "/api/v1/grants?limit=20",
                    "/api/v1/studies?limit=20", "/api/v1/search?q=cell", "/api/v1/review",
                    "/api/v1/candidates", "/api/v1/decisions", "/api/v1/groups",
                    "/api/v1/insight/figures/publications.count", "/api/v1/sources",
                    "/api/v1/opportunities", "/api/v1/me/opportunities", "/api/v1/teams",
                    "/api/v1/insight/figures/impact.studies", "/api/v1/inbox",
                    "/api/v1/health/ready"):  # fmt: skip
            get(url)
        for person in (people or {}).get("data", [])[:3]:
            get(f"/api/v1/people/{person['id']}")
            get(f"/api/v1/people/{person['id']}/publications")
            get(f"/api/v1/people/{person['id']}/profile")
            get(f"/api/v1/people/{person['id']}/impact")
            get(f"/api/v1/people/{person['id']}/cvs")
            get(f"/api/v1/people/{person['id']}/opportunities")
    return problems


def check(release: str, work: Path, postgres: str | None) -> list[str]:
    src = install(release, work)
    org = work / f"{release}-org"
    shutil.rmtree(org, ignore_errors=True)
    old_python = src / ".venv" / "bin" / "python"
    old_tessera = [str(old_python), "-m", "tessera"]
    run([str(old_python), str(REPO / "scripts" / "upgrade_seed.py"), str(src), str(org)],
        env=clean_env(), cwd=work)  # fmt: skip
    config_path = org / "tessera.toml"
    home = org / "var"

    from tessera.config import load_config

    url = load_config(config_path, env={}).ledger_url
    before = ledger_rows(url)  # before the backup, which notes itself in the ledger
    has_backup = "backup" in run([*old_tessera, "--help"], env=clean_env()).stdout
    archive = None
    if has_backup:
        archive = work / f"{release}-backup.tar.gz"
        archive.unlink(missing_ok=True)
        run([*old_tessera, "--config", str(config_path), "backup", "--out", str(archive)],
            env=clean_env())  # fmt: skip
    if postgres:  # the release itself moves its installation to PostgreSQL
        assert archive is not None, "moving to PostgreSQL needs the release's backup"
        reset_postgres(postgres)
        moved_org = work / f"{release}-pg-org"
        shutil.rmtree(moved_org, ignore_errors=True)
        moved_org.mkdir()
        (moved_org / "tessera.toml").write_text(
            config_path.read_text().replace('url = ""', f'url = "{postgres}"', 1)
        )
        run([*old_tessera, "--config", str(moved_org / "tessera.toml"), "restore", str(archive)],
            env=clean_env())  # fmt: skip
        org, config_path, home = moved_org, moved_org / "tessera.toml", moved_org / "var"
        url = load_config(config_path, env={}).ledger_url
        before = ledger_rows(url)
    kept = {"lake": digests(home / "lake"), "raw": digests(home / "raw")}

    env = clean_env()
    run([*TESSERA, "--config", str(config_path), "db", "upgrade"], env=env)
    for what in ("search", "discovery"):
        run([*TESSERA, "--config", str(config_path), "build", what], env=env)
    problems = compare_ledgers(before, ledger_rows(url), f"{release} upgraded")
    for zone, files in kept.items():
        now = digests(home / zone)
        changed = [f for f, d in files.items() if now.get(f) != d]
        if changed:
            problems.append(
                f"{release}: {len(changed)} files in {zone}/ changed, e.g. {changed[0]}"
            )
    doctor = subprocess.run([*TESSERA, "--config", str(config_path), "doctor"], env=env,
                            capture_output=True, text=True)  # fmt: skip
    if doctor.returncode != 0:
        problems.append(f"{release}: tessera doctor failed:\n{doctor.stdout[-1500:]}")
    problems += [f"{release}: {p}" for p in pages(config_path)]

    if archive is not None and not postgres:  # the release's backup, restored by this one
        restored = work / f"{release}-restored"
        shutil.rmtree(restored, ignore_errors=True)
        restored.mkdir()
        (restored / "tessera.toml").write_text((org / "tessera.toml").read_text())
        run([*TESSERA, "--config", str(restored / "tessera.toml"), "restore", str(archive)],
            env=env)  # fmt: skip
        run([*TESSERA, "--config", str(restored / "tessera.toml"), "build", "search"], env=env)
        again = load_config(restored / "tessera.toml", env={}).ledger_url
        problems += compare_ledgers(before, ledger_rows(again), f"{release} backup restored")
        problems += [f"{release} restored: {p}" for p in pages(restored / "tessera.toml")]
    count = sum(len(rows) for rows in before.values())
    print(f"{release}: {count:,} ledger rows in {len(before)} tables"
          f"{', on PostgreSQL' if postgres else ''}"
          f"{', and its backup restored' if archive is not None and not postgres else ''}: "
          f"{'all kept' if not problems else f'{len(problems)} problems'}", flush=True)  # fmt: skip
    return problems


def reset_postgres(url: str) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("releases", nargs="*", default=list(RELEASES))
    parser.add_argument("--postgres", help="an empty PostgreSQL database's URL, for 0.7.0")
    parser.add_argument("--work", type=Path, help="where to keep the releases (kept between runs)")
    args = parser.parse_args(list(argv) if argv is not None else None)
    work = args.work or Path(tempfile.gettempdir()) / "tessera-upgrades"
    work.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    for release in args.releases:
        problems += check(release, work.resolve(), None)
    if args.postgres:
        problems += check("v0.7.0", work.resolve(), args.postgres)
    for problem in problems:
        print("  " + problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
