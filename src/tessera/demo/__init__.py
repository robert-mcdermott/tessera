"""A fictional organization to explore Tessera with, and to take the documentation's screenshots.

``tessera demo`` builds a complete installation: a configuration describing the Corvane
Institute for Biomedical Research, and a data home holding its public records, written as
the connectors would write them under recorded fetch runs. Then the real evidence engine
runs, a scripted curator works through part of the review queue, merges most duplicate
records, confirms some ORCID iDs and decides on discovered people, and the search index and
discovery data are built. Every screen is real Tessera output about invented people.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine

from tessera.config import Config, example_config_text, load_config
from tessera.demo.world import CITY, DOMAIN, ORG, ORG_SHORT, REPORTER_ORG, World, build_world
from tessera.home import DataHome
from tessera.jobs import runs
from tessera.lake import LakeWriter
from tessera.lake.schemas import CTGOV, ICITE, ORCID, PUBMED, REPORTER, REPORTER_LINKS
from tessera.ledger import migrate
from tessera.ledger.db import make_engine

CURATOR = "Demo curator"

ORGANIZATION = f'''[organization]
name = "{ORG}"
short_name = "{ORG_SHORT}"
aliases = [
  "{ORG}",
  "Corvane Institute",
  "{ORG_SHORT} Medical Center",
]
affiliation_patterns = ["corvane inst*", "corvane medical center"]
reporter_org_names = ["{REPORTER_ORG}"]
ctgov_org_names = ["{ORG}", "{ORG_SHORT} Medical Center"]
email_domains = ["{DOMAIN}"]
exclusions = []

'''


class DemoError(Exception):
    pass


def demo_config_text() -> str:
    text = example_config_text()
    start = text.index("[organization]")
    end = text.index("# Re-branding")
    return text[:start] + ORGANIZATION + text[end:]


def _record(
    engine: Engine, home: DataHome, source: str, dataset: Any, records: list[dict], mode: str
) -> None:
    """Write records as a successful fetch run of ``source`` would."""
    run_id = runs.start_run(engine, source, "fetch", {"mode": mode, "demo": True})
    with LakeWriter(home.lake, dataset, run_id) as writer:
        writer.add(records)
    report = {
        "total_found": len(records),
        "expected": len(records),
        "payloads": max(1, len(records) // 200),
        "failure_count": 0,
        "notes": [],
    }
    runs.finish_run(engine, run_id, "succeeded", {"report": report, "lake": {}})


def build_demo(
    path: Path,
    *,
    seed: int = 7,
    force: bool = False,
    today: date | None = None,
    labs_per_area: int = 3,
) -> dict[str, Any]:
    """Create ``path/tessera.toml`` and a data home at ``path/var`` for the demo organization."""
    from tessera.evidence import store
    from tessera.evidence.people import import_roster
    from tessera.evidence.resolve import resolve
    from tessera.jobs.build import build_discovery, build_search

    today = today or date.today()
    path = Path(path)
    config_path = path / "tessera.toml"
    if config_path.exists() and not force:
        raise DemoError(f"{config_path} exists; use --force to replace the demo there.")
    path.mkdir(parents=True, exist_ok=True)
    var = path / "var"
    if var.exists():
        if not force:
            raise DemoError(f"{var} exists; use --force to replace it.")
        _remove(var)
    config_path.write_text(demo_config_text(), encoding="utf-8")
    config = load_config(config_path, env={})
    home = DataHome(config.data.home)
    home.create()
    engine = make_engine(config.ledger_url)
    try:
        migrate.upgrade(engine)
        world = build_world(seed, today, labs_per_area)
        _record(engine, home, "reporter", REPORTER, world.reporter, "organization")
        _record(engine, home, "reporter", REPORTER_LINKS, world.links, "links")
        _record(engine, home, "ctgov", CTGOV, world.studies, "organization")
        _record(engine, home, "pubmed", PUBMED, world.pubmed, "affiliation")
        _record(engine, home, "icite", ICITE, world.icite, "refresh")
        _record(engine, home, "orcid", ORCID, world.orcid, "refresh")

        roster = path / "roster.csv"
        _write_roster(world, roster)
        import_roster(engine, roster, today=today)  # before grants, as an organization would
        resolve(config, home, engine, today=today)
        merged = _merge_duplicates(engine, keep_open=2)
        resolve(config, home, engine, today=today)
        curated = _curate(engine, config, world)
        resolve(config, home, engine, today=today)
        _evaluate(engine, config)
        _accounts(engine, world)
        build_search(config, home, engine)
        built = build_discovery(config, home, engine)
        people = len(store.load_people(engine))
    finally:
        engine.dispose()
    return {
        "config": str(config_path),
        "people": people,
        "papers": len(world.pubmed),
        "grants": len({r["core_project_num"] for r in world.reporter}),
        "studies": len(world.studies),
        "merged": merged,
        **curated,
        "concepts": built.get("concepts", 0),
    }


def _remove(directory: Path) -> None:
    import shutil

    shutil.rmtree(directory)


def _write_roster(world: World, path: Path) -> None:
    lines = ["family,given,email,orcid,unit,title,start"]
    for row in world.roster:
        lines.append(
            ",".join(
                f'"{row[k]}"'
                for k in ("family", "given", "email", "orcid", "unit", "title", "start")
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _merge_duplicates(engine: Engine, keep_open: int) -> int:
    """Merge proposed duplicates as a curator would, keeping the grant record; leave a few
    for the Possible duplicates page."""
    from tessera.evidence import store
    from tessera.evidence.merge import merge_people

    people = {p.id: p for p in store.load_people(engine)}
    papers: dict[str, int] = {}
    for row in store.assertions_for(
        engine, predicate="authored", statuses=("suggested", "accepted")
    ):
        papers[row["subject_id"]] = papers.get(row["subject_id"], 0) + 1
    # The pairs left open are those with the fewest papers, so they stay out of the way.
    pairs = sorted(
        store.assertions_for(engine, predicate="same_as", statuses=("suggested",)),
        key=lambda r: -(papers.get(r["subject_id"], 0) + papers.get(r["object_id"], 0)),
    )
    merged = 0
    for row in pairs[:-keep_open] if len(pairs) > keep_open else []:
        a, b = row["subject_id"], row["object_id"]
        if a not in people or b not in people:
            continue
        # Keep the record that is current (the roster's), so a merge never retires anyone.
        keep, fold = (
            (a, b)
            if people[a].status == "active" and people[b].status != "active"
            else (
                (b, a)
                if people[b].status == "active" and people[a].status != "active"
                else ((a, b) if "nih_profile" in people[a].identifiers else (b, a))
            )
        )
        merge_people(engine, fold, keep, CURATOR)
        people.pop(fold)
        merged += 1
    return merged


def _key_of(engine: Engine, world: World) -> dict[str, str]:
    """Ledger person id -> world person key, by full name (insiders' family names are unique)."""
    from tessera.evidence import store

    by_name = {(p.family, p.given): p.key for p in world.people}
    out = {}
    for record in store.load_people(engine, statuses=("active", "former", "candidate")):
        for n in record.names:
            key = by_name.get((n.family, (n.given or "").split(" ")[0]))
            if key:
                out[record.id] = key
                break
    return out


def _decided(key: str, share: float) -> bool:
    """Whether the demo curator has worked through this person (stable for a world key)."""
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % 1000 < share * 1000


def _curate(engine: Engine, config: Config, world: World) -> dict[str, int]:
    """Work through part of the queue: about three people in four decided, with what is true."""
    from tessera.evidence import store

    key_of = _key_of(engine, world)
    accepted = rejected = 0
    for row in store.assertions_for(engine, predicate="authored", statuses=("suggested",)):
        person = row["subject_id"]
        if person not in key_of or not _decided(key_of[person], 0.75):
            continue
        if (row["score"] or 0) < 0.1:
            continue  # very unlikely: left alone, as a curator would
        truth = (key_of[person], row["object_id"].removeprefix("pub:")) in world.truth
        store.decide(engine, row["id"], "accept" if truth else "reject", CURATOR)
        accepted += truth
        rejected += not truth

    confirmed = 0
    for record in store.load_people(engine):
        for value in sorted(record.suggested.get("orcid", set())):
            if record.id in key_of and _decided(key_of[record.id], 0.5):
                store.decide_identifier(engine, record.id, "orcid", value, "accept", CURATOR)
                confirmed += 1

    promoted = dismissed = 0
    candidates = sorted(
        store.load_people(engine, statuses=("candidate",)), key=lambda p: p.display_name
    )
    for i, record in enumerate(candidates):
        if i % 2 == 0:
            store.decide_person(engine, record.id, "promote", CURATOR)
            promoted += 1
        elif i == 1:
            store.decide_person(engine, record.id, "dismiss", CURATOR, "Visiting researcher")
            dismissed += 1
    return {
        "accepted": accepted,
        "rejected": rejected,
        "orcid_confirmed": confirmed,
        "promoted": promoted,
        "dismissed": dismissed,
    }


def _evaluate(engine: Engine, config: Config) -> None:
    from tessera.evidence.model import evaluate

    evaluate(engine, config, actor=CURATOR)


def _accounts(engine: Engine, world: World) -> None:
    """A few accounts with each role, so Admin → Accounts has something to show. Their
    passwords are random and never shown; reset one with ``tessera users reset-password``."""
    from tessera.auth.accounts import Grant, create_account
    from tessera.evidence import store

    division = None
    with engine.connect() as conn:
        from sqlalchemy import select

        from tessera.ledger.tables import unit

        division = conn.execute(
            select(unit.c.id).where(unit.c.name == "Division of Neuroscience")
        ).scalar()
    key_of = _key_of(engine, world)
    researcher = next((pid for pid, key in sorted(key_of.items()) if key.startswith("p")), None)
    names = {p.id: p.display_name for p in store.load_people(engine)}
    accounts = [
        ("dana.admin", "Dana Whitfield", [Grant("admin")], None),
        ("lab.curator", "Rowan Ellery", [Grant("curator")], None),
        (
            "neuro.curator",
            "Tamsin Hale",
            [Grant("curator", "unit", division)] if division else [Grant("curator")],
            None,
        ),
        ("viewer", "Guest viewer", [Grant("viewer")], None),
    ]
    if researcher:
        accounts.append(
            (
                re.sub(r"[^a-z]", "", names.get(researcher, "researcher").lower())[:20]
                or "researcher",
                names.get(researcher, "Researcher"),
                [Grant("researcher")],
                researcher,
            )
        )
    for username, display, grants, person_id in accounts:
        create_account(
            engine,
            username,
            display,
            secrets.token_urlsafe(18),
            grants,
            actor=CURATOR,
            email=f"{username}@{DOMAIN}",
            person_id=person_id,
        )


__all__ = ["CITY", "DemoError", "build_demo", "demo_config_text"]
