"""``tessera sync``: bring everything up to date in the right order.

1. Fetch organization sweeps and linked records (grants, studies, PubMed sweep, linked papers).
2. Resolve people (so they can be searched for by name).
3. Read members' ORCID records, search PubMed by person name, fetch papers the ORCID
   records list, then fetch citation metrics.
4. Resolve everything: links, authorship suggestions, study officials.
5. Rebuild the search and serving index, then concept profiles and similar people.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy.engine import Engine

from tessera.config import Config
from tessera.connectors.base import IngestOptions
from tessera.evidence.resolve import ResolveResult, resolve
from tessera.home import DataHome
from tessera.jobs.build import build_profiles, build_search
from tessera.jobs.ingest import RunResult, ingest_many

FIRST = ("reporter", "ctgov", "pubmed", "pubmed:linked")
SECOND = ("orcid", "pubmed:people", "pubmed:linked", "icite")


@dataclass
class SyncResult:
    ingests: list[RunResult] = field(default_factory=list)
    resolves: list[ResolveResult] = field(default_factory=list)
    build: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        statuses = {r.status for r in self.ingests} | {r.status for r in self.resolves}
        if statuses <= {"succeeded"}:
            return "succeeded"
        return "failed" if statuses == {"failed"} else "partial"


def sync(
    config: Config,
    home: DataHome,
    engine: Engine,
    *,
    fetch: bool = True,
    options: IngestOptions | None = None,
    today: date | None = None,
    **ingest_kwargs: Any,
) -> SyncResult:
    result = SyncResult()
    options = options or IngestOptions()
    if fetch:
        result.ingests += ingest_many(
            config, home, engine, FIRST, options=options, today=today, **ingest_kwargs
        )
        result.resolves.append(resolve(config, home, engine, people_only=True, today=today))
        result.ingests += ingest_many(
            config, home, engine, SECOND, options=options, today=today, **ingest_kwargs
        )
    result.resolves.append(resolve(config, home, engine, today=today))
    result.build = build_search(config, home, engine)
    result.build["profiles"] = build_profiles(config, home, engine)
    return result
