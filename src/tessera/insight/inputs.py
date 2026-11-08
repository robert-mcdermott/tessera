"""The tables figures are computed from.

Gathering reads the lake and the ledger once, for the cohort's people only; computing a figure
reads nothing else. A snapshot keeps these tables, so its figures can be computed again,
exactly, long after the lake and the ledger have moved on (INT-03).

Each table has a fixed schema. ``members`` is always present: the cohort's people with the
years their work counts.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pyarrow as pa
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from tessera.config import Config
from tessera.connectors import CONNECTORS
from tessera.connectors.reporter import fiscal_year
from tessera.core.clock import isoformat, utc_now
from tessera.evidence.orgmatch import OrgMatcher
from tessera.insight.cohorts import Cohort
from tessera.jobs.runs import last_success
from tessera.lake import Lake
from tessera.ledger.tables import assertion, ingest_run

CHUNK = 500


def _schema(*fields: tuple[str, pa.DataType]) -> pa.Schema:
    return pa.schema(list(fields))


SCHEMAS: dict[str, pa.Schema] = {
    "paper_links": _schema(
        ("person_id", pa.string()), ("pmid", pa.string()), ("confirmed", pa.bool_()),
        ("score", pa.float64()), ("assertion_id", pa.string()),
    ),
    "papers": _schema(
        ("pmid", pa.string()), ("year", pa.int32()), ("title", pa.string()),
        ("journal", pa.string()), ("doi", pa.string()), ("retracted", pa.bool_()),
    ),
    "grant_links": _schema(
        ("person_id", pa.string()), ("core", pa.string()), ("assertion_id", pa.string()),
    ),
    "awards": _schema(
        ("appl_id", pa.int64()), ("core", pa.string()), ("project_num", pa.string()),
        ("fiscal_year", pa.int32()), ("subproject_id", pa.string()), ("title", pa.string()),
        ("activity", pa.string()), ("institute", pa.string()), ("mechanism", pa.string()),
        ("total", pa.int64()), ("direct", pa.int64()), ("indirect", pa.int64()),
        ("project_start", pa.date32()), ("project_end", pa.date32()),
        ("organization", pa.string()), ("ours", pa.bool_()),
    ),
    "group_members": _schema(
        ("group_id", pa.string()), ("name", pa.string()), ("slug", pa.string()),
        ("kind", pa.string()), ("person_id", pa.string()), ("start_year", pa.int32()),
        ("end_year", pa.int32()),
    ),
}  # fmt: skip

# Which source each table comes from, for the figure's data timestamps.
TABLE_SOURCES: dict[str, tuple[str, ...]] = {
    "paper_links": ("pubmed",),
    "papers": ("pubmed",),
    "grant_links": ("reporter",),
    "awards": ("reporter",),
}


@dataclass
class Inputs:
    tables: dict[str, pa.Table]
    sources: list[dict[str, Any]]  # each source with its last successful fetch
    gathered_at: datetime

    def rows(self, name: str) -> list[dict[str, Any]]:
        return self.tables[name].to_pylist() if name in self.tables else []

    def sources_for(self, names: Iterable[str]) -> list[dict[str, Any]]:
        wanted = set(names)
        return [s for s in self.sources if s["source"] in wanted]


@dataclass
class Context:
    engine: Engine
    lake: Lake
    config: Config
    cohort: Cohort


def _empty(name: str) -> pa.Table:
    return SCHEMAS[name].empty_table()


def _lake_join(lake: Lake, sql: str, keys: list[str], *, name: str = "_keys") -> list[dict]:
    """Run ``sql`` against the lake with ``_keys(key)`` holding the given values."""
    if not keys:
        return []
    cur = lake.cursor()
    try:
        cur.register(name, pa.table({"key": pa.array(sorted(set(keys)), pa.string())}))
        return cur.sql(sql).to_arrow_table().to_pylist()
    finally:
        cur.close()


def _links(ctx: Context, predicate: str) -> list[dict[str, Any]]:
    """Accepted links of the cohort's people, and strong suggestions not yet decided."""
    strong = ctx.config.evidence.strong_threshold
    people = sorted(ctx.cohort.members)
    out: list[dict[str, Any]] = []
    with ctx.engine.connect() as conn:
        for i in range(0, len(people), CHUNK):
            rows = conn.execute(
                select(assertion.c.id, assertion.c.subject_id, assertion.c.object_id,
                       assertion.c.status, assertion.c.score)
                .where(
                    assertion.c.predicate == predicate,
                    assertion.c.subject_id.in_(people[i : i + CHUNK]),
                    assertion.c.status.in_(("accepted", "suggested")),
                    assertion.c.reported.is_(True),
                )
            ).mappings()  # fmt: skip
            for r in rows:
                if r["status"] == "accepted" or (r["score"] or 0) >= strong:
                    out.append(dict(r))
    return out


def gather_paper_links(ctx: Context, tables: dict[str, pa.Table]) -> pa.Table:
    rows = _links(ctx, "authored")
    return pa.Table.from_pylist(
        [
            {
                "person_id": r["subject_id"],
                "pmid": r["object_id"].removeprefix("pub:"),
                "confirmed": r["status"] == "accepted",
                "score": r["score"],
                "assertion_id": r["id"],
            }
            for r in sorted(rows, key=lambda r: r["id"])
        ],
        schema=SCHEMAS["paper_links"],
    )


def gather_papers(ctx: Context, tables: dict[str, pa.Table]) -> pa.Table:
    pmids = tables["paper_links"].column("pmid").to_pylist() if "paper_links" in tables else []
    rows = _lake_join(
        ctx.lake,
        "SELECT a.pmid, a.pub_year AS year, a.title, a.journal, a.doi, "
        "coalesce(a.is_retracted, false) AS retracted FROM pubmed_article a "
        "JOIN _keys k ON k.key = a.pmid ORDER BY a.pmid",
        pmids,
    )
    return pa.Table.from_pylist(rows, schema=SCHEMAS["papers"])


def gather_grant_links(ctx: Context, tables: dict[str, pa.Table]) -> pa.Table:
    """Members' grants, from the funder's investigator lists (always accepted links)."""
    people = sorted(ctx.cohort.members)
    rows: list[dict[str, Any]] = []
    with ctx.engine.connect() as conn:
        for i in range(0, len(people), CHUNK):
            for r in conn.execute(
                select(assertion.c.id, assertion.c.subject_id, assertion.c.object_id).where(
                    assertion.c.predicate == "investigator_on",
                    assertion.c.subject_id.in_(people[i : i + CHUNK]),
                    assertion.c.status == "accepted",
                )
            ).mappings():
                rows.append(
                    {"person_id": r["subject_id"], "core": r["object_id"].removeprefix("grt:"),
                     "assertion_id": r["id"]}
                )  # fmt: skip
    rows.sort(key=lambda r: r["assertion_id"])
    return pa.Table.from_pylist(rows, schema=SCHEMAS["grant_links"])


# A constant list of column names: the queries below are built only from these strings.
AWARD_COLUMNS = (
    "appl_id, core_project_num AS core, project_num, fiscal_year, subproject_id, title, "
    "activity_code AS activity, agency_ic_admin AS institute, funding_mechanism AS mechanism, "
    "award_amount AS total, direct_cost AS direct, indirect_cost AS indirect, project_start, "
    "project_end, org_name AS organization"
)


def gather_awards(ctx: Context, tables: dict[str, pa.Table]) -> pa.Table:
    """Fiscal-year records of the members' grants and, for the organization, every award to
    it. ``ours`` says whether the awardee is the organization, decided here once."""
    cores = tables["grant_links"].column("core").to_pylist() if "grant_links" in tables else []
    rows = _lake_join(
        ctx.lake,
        f"SELECT {AWARD_COLUMNS} FROM reporter_project p JOIN _keys k ON k.key = "  # noqa: S608
        "p.core_project_num",
        cores,
    )
    if ctx.cohort.organization:
        seen = {r["appl_id"] for r in rows}
        rows += [
            r for r in ctx.lake.rows(
                f"SELECT {AWARD_COLUMNS} FROM reporter_project "  # noqa: S608
                "WHERE core_project_num IS NOT NULL"
            )
            if r["appl_id"] not in seen
        ]  # fmt: skip
    matcher = OrgMatcher(ctx.config.organization)
    verdicts: dict[str | None, bool] = {}
    out = []
    for r in rows:
        name = r["organization"]
        if name not in verdicts:
            verdicts[name] = matcher.matches(name)
        out.append({**r, "ours": verdicts[name]})
    if ctx.cohort.organization:
        out = [r for r in out if r["ours"] or r["core"] in set(cores)]
    out.sort(key=lambda r: r["appl_id"])
    return pa.Table.from_pylist(out, schema=SCHEMAS["awards"])


def gather_group_members(ctx: Context, tables: dict[str, pa.Table]) -> pa.Table:
    """Every active group's members among the cohort, with the years they were members."""
    from tessera.groups import store as groups
    from tessera.ledger.tables import unit

    people = set(ctx.cohort.members)
    rows: list[dict[str, Any]] = []
    with ctx.engine.connect() as conn:
        info = {
            r.id: r for r in conn.execute(
                select(unit.c.id, unit.c.name, unit.c.slug, unit.c.kind).where(
                    unit.c.status == "active")
            )
        }  # fmt: skip
        for m in groups.memberships(conn, group_ids=sorted(info)):
            if m["subject_id"] not in people:
                continue
            q = m["qualifiers"] or {}
            g = info[m["object_id"]]
            rows.append(
                {"group_id": g.id, "name": g.name, "slug": g.slug, "kind": g.kind,
                 "person_id": m["subject_id"], "start_year": _year(q.get("start")),
                 "end_year": _year(q.get("end"))}
            )  # fmt: skip
    rows.sort(key=lambda r: (r["group_id"], r["person_id"], r["start_year"] or 0))
    return pa.Table.from_pylist(rows, schema=SCHEMAS["group_members"])


def _year(text: Any) -> int | None:
    try:
        return int(str(text)[:4]) if text else None
    except ValueError:
        return None


GATHERERS: dict[str, Callable[[Context, dict[str, pa.Table]], pa.Table]] = {
    "paper_links": gather_paper_links,
    "papers": gather_papers,
    "grant_links": gather_grant_links,
    "awards": gather_awards,
    "group_members": gather_group_members,
}
REQUIRES: dict[str, tuple[str, ...]] = {"papers": ("paper_links",), "awards": ("grant_links",)}


def _ordered(needs: Iterable[str]) -> list[str]:
    out: list[str] = []

    def visit(name: str) -> None:
        if name in out:
            return
        if name not in GATHERERS:
            raise KeyError(f"no input table {name!r}")
        for dep in REQUIRES.get(name, ()):
            visit(dep)
        out.append(name)

    for name in needs:
        visit(name)
    return out


# Sources whose first sync reaches back ``sync.window_years``: years before that are partial.
WINDOWED = ("pubmed", "reporter")


def coverage(engine: Engine, config: Config) -> dict[str, int]:
    """The first year each windowed source covers completely (fiscal years for RePORTER)."""
    query = (
        select(ingest_run.c.source, func.min(ingest_run.c.started_at))
        .where(
            ingest_run.c.status.in_(("succeeded", "partial")),
            ingest_run.c.kind != "replay",
            ingest_run.c.source.in_(WINDOWED),
        )
        .group_by(ingest_run.c.source)
    )
    years = config.sync.window_years
    out: dict[str, int] = {}
    with engine.connect() as conn:
        for source, started in conn.execute(query).all():
            if started is None:
                continue
            day = started.date()
            # PubMed's window starts on the day of the first sync, so that year is partial;
            # RePORTER's covers whole fiscal years.
            out[source] = fiscal_year(day) - years if source == "reporter" else day.year - years + 1
    return out


def source_stamps(
    engine: Engine, names: Iterable[str], config: Config | None = None
) -> list[dict[str, Any]]:
    latest = last_success(engine)
    covered = coverage(engine, config) if config is not None else {}
    out = []
    for name in sorted(set(names)):
        connector = CONNECTORS.get(name)
        when = latest.get(name)
        stamp: dict[str, Any] = {
            "source": name,
            "label": connector.label if connector else name,
            "last_success_at": isoformat(when) if when else None,
        }
        if name in covered:
            stamp["complete_from"] = covered[name]
            stamp["period"] = "fiscal" if name == "reporter" else "calendar"
        out.append(stamp)
    return out


def gather(ctx: Context, needs: Iterable[str]) -> Inputs:
    tables: dict[str, pa.Table] = {"members": ctx.cohort.table()}
    for name in _ordered(needs):
        tables[name] = GATHERERS[name](ctx, tables)
    sources = {s for name in tables for s in TABLE_SOURCES.get(name, ())}
    return Inputs(tables, source_stamps(ctx.engine, sources, ctx.config), utc_now())


__all__ = ["GATHERERS", "SCHEMAS", "Context", "Inputs", "coverage", "gather", "source_stamps"]
