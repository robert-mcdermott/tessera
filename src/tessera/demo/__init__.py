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
from tessera.lake.schemas import CTGOV, ICITE, MESH, ORCID, PUBMED, REPORTER, REPORTER_LINKS
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
    from tessera.demo.groups import registry
    from tessera.evidence import store
    from tessera.evidence.people import import_roster
    from tessera.evidence.resolve import resolve
    from tessera.groups.importer import import_groups
    from tessera.jobs.build import build_attribution, build_discovery, build_search

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
        from tessera.demo.mesh import mesh_records

        _record(engine, home, "mesh", MESH, mesh_records(today.year), "annual")

        roster = path / "roster.csv"
        _write_roster(world, roster)
        # Before grants, as an organization would.
        import_roster(engine, roster, today=today, vocabulary=config.vocabulary)
        # Labs, programs, and the shared resource come from the Institute's registry.
        import_groups(
            engine, registry(world, today), source="registry", vocabulary=config.vocabulary,
            actor="Registry import", today=today,
        )  # fmt: skip
        resolve(config, home, engine, today=today)
        merged = _merge_duplicates(engine, keep_open=2)
        resolve(config, home, engine, today=today)
        curated = _curate(engine, config, world)
        resolve(config, home, engine, today=today)
        _evaluate(engine, config)
        _accounts(engine, world)
        build_search(config, home, engine)
        built = build_discovery(config, home, engine)
        build_attribution(config, home, engine, today=today)
        _attribute(engine)
        _pages(engine, today)
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
    lines = ["family,given,email,orcid,unit,kind,title,start"]
    for row in world.roster:
        values = [row[k] for k in ("family", "given", "email", "orcid", "unit")]
        values += ["division", row["title"], row["start"]]  # the roster's units are divisions
        lines.append(",".join(f'"{v}"' for v in values))
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


def _attribute(engine: Engine) -> None:
    """Decide group attributions as each group's people would: the papers with the strongest
    evidence are the group's, weak ones are not, and the newest few wait for review."""
    from sqlalchemy import select

    from tessera.evidence import store
    from tessera.ledger.tables import assertion

    with engine.connect() as conn:
        rows = conn.execute(
            select(assertion.c.id, assertion.c.object_id, assertion.c.subject_id,
                   assertion.c.score).where(
                assertion.c.predicate == "attributed_to", assertion.c.status == "suggested"
            )
        ).all()  # fmt: skip
    by_group: dict[str, list[Any]] = {}
    for row in rows:
        by_group.setdefault(row.object_id, []).append(row)
    for items in by_group.values():
        items.sort(key=lambda r: r.subject_id)  # PMIDs rise with time in the demo
        waiting = {r.id for r in items[-2:]}
        for i, r in enumerate(items):
            if r.id in waiting:
                continue
            if (r.score or 0) >= 0.7:
                store.decide(engine, r.id, "accept", CURATOR)
            elif (r.score or 0) < 0.6 or i % 2 == 0:
                store.decide(engine, r.id, "reject", CURATOR, "A member's work elsewhere")


def _pages(engine: Engine, today: date) -> None:
    """Published pages for the immunotherapy program and the lab with the most papers, as
    their managers would write them."""
    from sqlalchemy import func, select

    from tessera.groups import pages
    from tessera.ledger.tables import assertion, unit

    with engine.begin() as conn:
        lab = conn.execute(
            select(unit.c.id, unit.c.name)
            .join(assertion, assertion.c.object_id == unit.c.id)
            .where(
                unit.c.kind == "lab",
                assertion.c.predicate == "attributed_to",
                assertion.c.status == "accepted",
            )
            .group_by(unit.c.id, unit.c.name)
            .order_by(func.count().desc(), unit.c.name)
            .limit(1)
        ).first()
        program = conn.execute(
            select(unit.c.id).where(unit.c.slug == "cancer-immunotherapy-program")
        ).scalar()
        for group_id, name in [(program, "Cancer Immunotherapy Program"), *([lab] if lab else [])]:
            if not group_id:
                continue
            featured = conn.execute(
                select(assertion.c.subject_id)
                .where(
                    assertion.c.predicate == "attributed_to",
                    assertion.c.status == "accepted",
                    assertion.c.object_id == group_id,
                )
                .order_by(assertion.c.score.desc(), assertion.c.subject_id.desc())
                .limit(2)
            ).scalars()
            about = (
                f"The {name} brings together people who share questions, data, and equipment. "
                "We meet every other week to share results, and we train students and fellows "
                "across the Institute.\n\nVisitors are welcome: write to us to arrange a visit "
                "or a seminar."
            )
            blocks = [
                {"type": "about", "text": about},
                {"type": "people"},
                {"type": "publications", "featured": [p.removeprefix("pub:") for p in featured]},
                {"type": "themes"},
                {"type": "openings"},
                {"type": "news", "items": [
                    {"date": today.replace(day=1).isoformat(), "title": "Welcome, new fellows",
                     "text": "Three postdoctoral fellows joined this autumn."},
                    {"date": f"{today.year}-05-14", "title": "Retreat at the coast",
                     "text": "Posters, a keynote, and a long walk on the beach."},
                ]},
                {"type": "grants"},
                {"type": "topics"},
                {"type": "connections"},
                {"type": "contact", "text": "Seminars are open to everyone at the Institute."},
            ]  # fmt: skip
            pages.save_draft(conn, group_id, blocks, actor=CURATOR)
            pages.publish(conn, group_id, actor=CURATOR, note="First version")
        if program:
            _program_work(conn, program, today)
        # Every inbox was reviewed when the demo was built; one roster change came after.
        from tessera.groups import inbox

        for group_id in conn.execute(select(unit.c.id)).scalars():
            inbox.mark_reviewed(conn, group_id, actor=CURATOR)
        if lab:
            _late_arrival(conn, lab.id, today)


def _program_work(conn: Any, program: str, today: date) -> None:
    """Themes and openings for the immunotherapy program."""
    from datetime import timedelta

    from sqlalchemy import select

    from tessera.groups import openings, store, themes
    from tessera.ledger.tables import assertion

    people = sorted(store.current_members(conn, [program], today))
    grants = sorted(
        set(
            conn.execute(
                select(assertion.c.object_id).where(
                    assertion.c.predicate == "investigator_on",
                    assertion.c.status == "accepted",
                    assertion.c.subject_id.in_(people),
                )
            ).scalars()
        )
    )
    papers = list(
        conn.execute(
            select(assertion.c.subject_id)
            .where(
                assertion.c.predicate == "attributed_to",
                assertion.c.status == "accepted",
                assertion.c.object_id == program,
            )
            .order_by(assertion.c.subject_id.desc())
            .limit(6)
        ).scalars()
    )
    themes.create_theme(
        conn, program,
        {"name": "Engineered T cells for solid tumors", "kind": "theme",
         "summary": "Why cell therapies that work in blood cancers stall in solid tumors, and "
         "how to change the microenvironment so they do not."},
        actor=CURATOR, links=[*people[:3], *grants[:2], *papers[:3]],
    )  # fmt: skip
    themes.create_theme(
        conn, program,
        {"name": "A shared atlas of tumor-infiltrating lymphocytes", "kind": "project",
         "summary": "One single-cell atlas of the T cells found in tumors across the program's "
         "studies, open to every lab."},
        actor=CURATOR, links=[*people[2:5], *papers[3:6]],
    )  # fmt: skip
    openings.create_opening(
        conn, program,
        {"title": "Postdoctoral fellow in cell therapy", "kind": "job",
         "closes_on": today + timedelta(days=10),
         "description": "Join a team building and testing engineered T cells. Experience with "
         "flow cytometry or single-cell methods is welcome.",
         "url": "https://www.corvane.example/careers/cell-therapy-fellow",
         "contact": "cip@corvane.example"},
        actor=CURATOR, today=today - timedelta(days=30),
    )  # fmt: skip
    openings.create_opening(
        conn, program,
        {"title": "Summer research internship", "kind": "training",
         "closes_on": today + timedelta(days=75),
         "description": "Ten weeks in a program lab for undergraduates, with a stipend.",
         "contact": "cip@corvane.example"},
        actor=CURATOR, today=today,
    )  # fmt: skip


def _late_arrival(conn: Any, lab: str, today: date) -> None:
    """Someone joins a lab after its inbox was reviewed, so the inbox has a roster change."""
    from sqlalchemy import select

    from tessera.config import VocabularyConfig
    from tessera.groups import store
    from tessera.ledger.tables import person

    here = store.current_members(conn, [lab], today)
    newcomer = conn.execute(
        select(person.c.id)
        .where(person.c.status == "active", person.c.id.notin_(sorted(here)))
        .order_by(person.c.family_name)
        .limit(1)
    ).scalar()
    if newcomer:
        store.add_membership(
            conn, person_id=newcomer, group_id=lab, vocabulary=VocabularyConfig(),
            actor="Registry import", role="affiliate", start=today.isoformat()[:7],
            source="registry",
        )  # fmt: skip


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
        program = conn.execute(
            select(unit.c.id).where(unit.c.slug == "cancer-immunotherapy-program")
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
    if program:  # the program's coordinator keeps its page and decides its papers
        accounts.append(
            ("cip.manager", "Wren Castellan", [Grant("manager", "unit", program, True)], None)
        )
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
