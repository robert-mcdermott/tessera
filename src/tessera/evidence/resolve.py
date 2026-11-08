"""Run the evidence engine: people, links, and authorship, recorded as one run."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from tessera.config import Config
from tessera.evidence import authorship, duplicates, links, people
from tessera.evidence.orgmatch import OrgMatcher
from tessera.home import DataHome
from tessera.jobs import runs
from tessera.jobs.lock import job_lock
from tessera.lake import Lake
from tessera.ledger.tables import person

log = logging.getLogger("tessera.evidence")


def directory_counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as conn:
        rows = conn.execute(select(person.c.status, func.count()).group_by(person.c.status)).all()
    return {row[0]: row[1] for row in rows}


@dataclass
class ResolveResult:
    run_id: str
    status: str
    counts: dict[str, Any]


def resolve(
    config: Config,
    home: DataHome,
    engine: Engine,
    *,
    people_only: bool = False,
    today: date | None = None,
) -> ResolveResult:
    """Bring people, links, and suggestions up to date with the lake.

    ``people_only`` stops after people (used mid-sync, before searching PubMed by name).
    """
    home.require()
    today = today or date.today()
    with job_lock(home.root):
        runs.mark_interrupted(engine)
        run_id = runs.start_run(engine, "evidence", "resolve", {"people_only": people_only})
        counts: dict[str, Any] = {}
        try:
            with Lake(home.lake) as lake:
                matcher = OrgMatcher(config.organization)
                counts["people"] = people.bootstrap_from_reporter(
                    engine, lake, matcher, today
                ).to_json()
                slots = people.affiliated_slots(lake, matcher, config)
                counts["people"]["promoted"] = people.promote_investigators(
                    engine, slots, config.evidence.discover_min_papers
                )
                counts["discovery"] = people.discover_candidates(
                    engine, slots, config.evidence.discover_min_papers
                ).to_json()
                if not people_only:
                    link_stats = links.LinkStats()
                    links.sync_investigators(engine, lake, link_stats)
                    links.sync_source_links(engine, lake, link_stats)
                    counts["authorship"] = authorship.resolve_authorship(
                        engine, lake, config, matcher
                    ).to_json()
                    links.resolve_officials(engine, lake, config, matcher, link_stats)
                    counts["links"] = link_stats.to_json()
                    counts["duplicates"] = duplicates.resolve_duplicates(
                        engine, lake, config
                    ).to_json()
        except BaseException as exc:
            runs.finish_run(engine, run_id, "failed", counts, f"{exc.__class__.__name__}: {exc}")
            raise
        counts["directory"] = directory_counts(engine)
        runs.finish_run(engine, run_id, "succeeded", counts)
        log.info("resolve run %s succeeded", run_id)
        return ResolveResult(run_id, "succeeded", counts)
