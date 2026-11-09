"""The tables figures are computed from.

Gathering reads the lake and the ledger once, for the cohort's people only; computing a figure
reads nothing else. A snapshot keeps these tables, so its figures can be computed again,
exactly, long after the lake and the ledger have moved on (INT-03).

Each table has a fixed schema. ``members`` is always present: the cohort's people with the
years their work counts.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import Any

import pyarrow as pa
from sqlalchemy import and_, func, or_, select
from sqlalchemy.engine import Engine

from tessera.config import Config
from tessera.connectors import CONNECTORS
from tessera.connectors.reporter import fiscal_year
from tessera.core.clock import isoformat, utc_now
from tessera.core.once import Once
from tessera.evidence.orgmatch import OrgMatcher
from tessera.insight.cohorts import Cohort
from tessera.jobs.runs import last_success
from tessera.lake import Lake
from tessera.ledger.tables import (
    assertion,
    curation_event,
    ingest_run,
    person_event,
    unit_event,
)

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
    "paper_topics": _schema(
        ("pmid", pa.string()), ("descriptor_ui", pa.string()), ("descriptor", pa.string()),
    ),
    "mesh_areas": _schema(
        ("descriptor_ui", pa.string()), ("area_tree", pa.string()), ("area", pa.string()),
    ),
    "citations": _schema(
        ("pmid", pa.string()), ("rcr", pa.float64()), ("citations", pa.int32()),
        ("research", pa.bool_()),
    ),
    "study_links": _schema(
        ("person_id", pa.string()), ("nct_id", pa.string()), ("confirmed", pa.bool_()),
        ("score", pa.float64()), ("assertion_id", pa.string()),
    ),
    "clinical_citations": _schema(
        ("pmid", pa.string()), ("citing_pmid", pa.string()), ("year", pa.int32()),
        ("kind", pa.string()),
    ),
    "study_references": _schema(
        ("pmid", pa.string()), ("nct_id", pa.string()), ("assertion_id", pa.string()),
    ),
    "studies": _schema(
        ("nct_id", pa.string()), ("title", pa.string()), ("status", pa.string()),
        ("phases", pa.list_(pa.string())), ("study_type", pa.string()),
        ("sponsor", pa.string()), ("sponsor_class", pa.string()),
        ("intervention_types", pa.list_(pa.string())), ("conditions", pa.list_(pa.string())),
        ("condition_ui", pa.list_(pa.string())), ("start_date", pa.date32()),
        ("completion_date", pa.date32()), ("enrollment", pa.int32()),
        ("enrollment_type", pa.string()), ("ours", pa.bool_()),
    ),
}  # fmt: skip

# Which source each table comes from, for the figure's data timestamps.
TABLE_SOURCES: dict[str, tuple[str, ...]] = {
    "paper_links": ("pubmed",),
    "papers": ("pubmed",),
    "grant_links": ("reporter",),
    "awards": ("reporter",),
    "paper_topics": ("pubmed",),
    "mesh_areas": ("mesh",),
    "citations": ("icite",),
    "clinical_citations": ("icite", "pubmed"),
    "study_references": ("ctgov",),
    "study_links": ("ctgov",),
    "studies": ("ctgov",),
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
    columns = ("id", "subject_id", "object_id", "status", "score")
    out: list[dict[str, Any]] = []
    with ctx.engine.connect() as conn:
        for i in range(0, len(people), CHUNK):
            # All at once (fetchall), and only what counts: row by row, a large organization's
            # links took most of a figure's time.
            rows = conn.execute(
                select(*(assertion.c[c] for c in columns)).where(
                    assertion.c.predicate == predicate,
                    assertion.c.subject_id.in_(people[i : i + CHUNK]),
                    assertion.c.reported.is_(True),
                    or_(
                        assertion.c.status == "accepted",
                        and_(assertion.c.status == "suggested", assertion.c.score >= strong),
                    ),
                )
            ).all()
            out.extend(dict(zip(columns, r, strict=True)) for r in rows)
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


def gather_paper_topics(ctx: Context, tables: dict[str, pa.Table]) -> pa.Table:
    """The major MeSH topics of the cohort's papers."""
    pmids = tables["paper_links"].column("pmid").to_pylist() if "paper_links" in tables else []
    rows = _lake_join(
        ctx.lake,
        "SELECT m.pmid, m.descriptor_ui, m.descriptor FROM pubmed_mesh m JOIN _keys k "
        "ON k.key = m.pmid WHERE coalesce(m.major_topic, false) ORDER BY m.pmid, m.descriptor_ui",
        pmids,
    )
    return pa.Table.from_pylist(rows, schema=SCHEMAS["paper_topics"])


def gather_citations(ctx: Context, tables: dict[str, pa.Table]) -> pa.Table:
    pmids = tables["paper_links"].column("pmid").to_pylist() if "paper_links" in tables else []
    rows = _lake_join(
        ctx.lake,
        "SELECT c.pmid, c.relative_citation_ratio AS rcr, c.citation_count AS citations, "
        "c.is_research_article AS research FROM icite_metrics c JOIN _keys k ON k.key = c.pmid "
        "ORDER BY c.pmid",
        pmids,
    )
    return pa.Table.from_pylist(rows, schema=SCHEMAS["citations"])


def gather_clinical_citations(ctx: Context, tables: dict[str, pa.Table]) -> pa.Table:
    """The clinical articles citing the cohort's papers (iCite), with their year and kind.

    The kind is fixed here, when the inputs are gathered, so a snapshot keeps it."""
    from tessera.impact.threads import citing_articles

    pmids = tables["paper_links"].column("pmid").to_pylist() if "paper_links" in tables else []
    pairs = _lake_join(
        ctx.lake,
        "SELECT c.pmid, c.citing_pmid FROM icite_clinical_citation c "
        "JOIN _keys k ON k.key = c.pmid",
        pmids,
    )
    meta = citing_articles(ctx.lake, {r["citing_pmid"] for r in pairs})
    rows = [
        {"pmid": r["pmid"], "citing_pmid": r["citing_pmid"],
         "year": meta[r["citing_pmid"]]["year"], "kind": meta[r["citing_pmid"]]["kind"]}
        for r in pairs
        if not meta[r["citing_pmid"]]["retracted"]
    ]  # fmt: skip
    rows.sort(key=lambda r: (r["pmid"], r["citing_pmid"]))
    return pa.Table.from_pylist(rows, schema=SCHEMAS["clinical_citations"])


def gather_study_references(ctx: Context, tables: dict[str, pa.Table]) -> pa.Table:
    """Registered studies that list the cohort's papers (accepted, still reported)."""
    pmids = tables["paper_links"].column("pmid").to_pylist() if "paper_links" in tables else []
    refs = sorted({f"pub:{p}" for p in pmids})
    rows: list[dict[str, Any]] = []
    with ctx.engine.connect() as conn:
        for i in range(0, len(refs), CHUNK):
            for r in conn.execute(
                select(assertion.c.id, assertion.c.subject_id, assertion.c.object_id).where(
                    assertion.c.predicate == "references",
                    assertion.c.object_id.in_(refs[i : i + CHUNK]),
                    assertion.c.status == "accepted",
                    assertion.c.reported.is_(True),
                )
            ).mappings():
                rows.append({"pmid": r["object_id"].removeprefix("pub:"),
                             "nct_id": r["subject_id"].removeprefix("stu:"),
                             "assertion_id": r["id"]})  # fmt: skip
    rows.sort(key=lambda r: (r["pmid"], r["nct_id"]))
    return pa.Table.from_pylist(rows, schema=SCHEMAS["study_references"])


# Research areas are the tops of the MeSH branches that say what someone studies.
AREA_CATEGORIES = ("A", "B", "C", "F", "G")


def gather_mesh_areas(ctx: Context, tables: dict[str, pa.Table]) -> pa.Table:
    """Each heading the figures use, with the research areas above it (when MeSH is loaded)."""
    wanted: set[str] = set()
    if "paper_topics" in tables:
        wanted |= set(tables["paper_topics"].column("descriptor_ui").to_pylist())
    if "studies" in tables:
        for uis in tables["studies"].column("condition_ui").to_pylist():
            wanted |= set(uis or [])
    rows: list[dict[str, Any]] = []
    try:
        found = _lake_join(
            ctx.lake,
            "SELECT d.descriptor_ui, d.tree_numbers FROM mesh_descriptor d JOIN _keys k "
            "ON k.key = d.descriptor_ui",
            sorted(wanted),
        )
        tops = sorted({t.split(".")[0] for r in found for t in r["tree_numbers"] or []
                       if t[0] in AREA_CATEGORIES})  # fmt: skip
        named = {}
        if tops:
            cur = ctx.lake.cursor()
            try:
                cur.register("_tops", pa.table({"tree": pa.array(tops, pa.string())}))
                for name, tree in cur.sql(
                    "SELECT d.name, t.tree FROM mesh_descriptor d, _tops t "
                    "WHERE list_contains(d.tree_numbers, t.tree)"
                ).fetchall():
                    named[tree] = name
            finally:
                cur.close()
        for r in found:
            for top in sorted({t.split(".")[0] for t in r["tree_numbers"] or []
                               if t[0] in AREA_CATEGORIES}):  # fmt: skip
                rows.append({"descriptor_ui": r["descriptor_ui"], "area_tree": top,
                             "area": named.get(top, top)})  # fmt: skip
    except Exception:  # without MeSH data there are no areas, only headings
        rows = []
    rows.sort(key=lambda r: (r["descriptor_ui"], r["area_tree"]))
    return pa.Table.from_pylist(rows, schema=SCHEMAS["mesh_areas"])


def gather_study_links(ctx: Context, tables: dict[str, pa.Table]) -> pa.Table:
    rows = _links(ctx, "official_of")
    return pa.Table.from_pylist(
        [
            {"person_id": r["subject_id"], "nct_id": r["object_id"].removeprefix("stu:"),
             "confirmed": r["status"] == "accepted", "score": r["score"], "assertion_id": r["id"]}
            for r in sorted(rows, key=lambda r: r["id"])
        ],
        schema=SCHEMAS["study_links"],
    )  # fmt: skip


STUDY_COLUMNS = (
    "nct_id, brief_title AS title, overall_status AS status, phases, study_type, "
    "lead_sponsor AS sponsor, lead_sponsor_class AS sponsor_class, "
    "list_transform(coalesce(interventions, []), lambda x: x.type) AS intervention_types, "
    "conditions, list_transform(coalesce(condition_mesh, []), lambda x: x.id) AS condition_ui, "
    "start_date, completion_date, enrollment, enrollment_type"
)


def gather_studies(ctx: Context, tables: dict[str, pa.Table]) -> pa.Table:
    """The cohort's studies and, for the organization, every study registered under it."""
    linked = tables["study_links"].column("nct_id").to_pylist() if "study_links" in tables else []
    rows = _lake_join(
        ctx.lake,
        f"SELECT {STUDY_COLUMNS} FROM ctgov_study s JOIN _keys k ON k.key = s.nct_id",  # noqa: S608
        linked,
    )
    for r in rows:
        r["ours"] = False
    if ctx.cohort.organization:
        seen = {r["nct_id"] for r in rows}
        for r in ctx.lake.rows(f"SELECT {STUDY_COLUMNS} FROM ctgov_study"):  # noqa: S608
            if r["nct_id"] not in seen:
                rows.append({**r, "ours": True})
        for r in rows:
            r["ours"] = True  # in the lake, the organization's registry search found them
    rows.sort(key=lambda r: r["nct_id"])
    return pa.Table.from_pylist(rows, schema=SCHEMAS["studies"])


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
    "paper_topics": gather_paper_topics,
    "citations": gather_citations,
    "clinical_citations": gather_clinical_citations,
    "study_references": gather_study_references,
    "study_links": gather_study_links,
    "studies": gather_studies,
    "mesh_areas": gather_mesh_areas,
}
REQUIRES: dict[str, tuple[str, ...]] = {
    "papers": ("paper_links",),
    "awards": ("grant_links",),
    "paper_topics": ("paper_links",),
    "citations": ("paper_links",),
    "clinical_citations": ("paper_links",),
    "study_references": ("paper_links",),
    "studies": ("study_links",),
}


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


# --- remembering inputs ---------------------------------------------------------------------
#
# Every figure on a page gathers the same inputs for the same cohort, and they change only when
# the data does. Gathered tables are kept, keyed by what they were gathered from: the ledger and
# lake, the cohort (its members and their dates), and the data's version, which moves with every
# run (fetch, resolve, build), decision, and change to a person or group. Bounded by size; the
# least recently used go first.

CACHE_BYTES = 512 * 2**20
CACHE_SECONDS = 600  # and never older than this, whatever the logs say
_cache: OrderedDict[tuple[Any, ...], tuple[float, pa.Table]] = OrderedDict()
_cache_lock = threading.Lock()


def data_version(engine: Engine) -> tuple[Any, ...]:
    """What the gathered inputs depend on, cheaply: the newest row of each log."""
    with engine.connect() as conn:
        return tuple(
            conn.execute(select(func.max(column))).scalar()
            for column in (ingest_run.c.id, curation_event.c.id, person_event.c.id, unit_event.c.id)
        )


def _cohort_digest(members: pa.Table) -> str:
    digest = hashlib.sha256()
    for column in members.columns:
        digest.update(repr(column.to_pylist()).encode())
    return digest.hexdigest()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _remember(key: tuple[Any, ...], table: pa.Table) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic(), table)
        _cache.move_to_end(key)
        while sum(t.nbytes for _, t in _cache.values()) > CACHE_BYTES and len(_cache) > 1:
            _cache.popitem(last=False)


def _recall(key: tuple[Any, ...]) -> pa.Table | None:
    with _cache_lock:
        found = _cache.get(key)
        if found is None:
            return None
        if time.monotonic() - found[0] > CACHE_SECONDS:
            del _cache[key]
            return None
        _cache.move_to_end(key)
        return found[1]


_gathering = Once()


def _gather_one(
    key: tuple[Any, ...], name: str, ctx: Context, tables: dict[str, pa.Table]
) -> pa.Table:
    found = _recall(key)  # gathered while this request waited its turn
    if found is None:
        found = GATHERERS[name](ctx, tables)
        _remember(key, found)
    return found


def gather(ctx: Context, needs: Iterable[str]) -> Inputs:
    tables: dict[str, pa.Table] = {"members": ctx.cohort.table()}
    base = (
        str(ctx.engine.url),
        str(ctx.lake.root),
        ctx.config.evidence.strong_threshold,
        _cohort_digest(tables["members"]),
        data_version(ctx.engine),
    )
    for name in _ordered(needs):
        key = (*base, name)
        found = _recall(key)
        if found is None:
            # Others asking for the same inputs now wait for this one gathering.
            found = _gathering.run(key, partial(_gather_one, key, name, ctx, dict(tables)))
        tables[name] = found
    sources = {s for name in tables for s in TABLE_SOURCES.get(name, ())}
    return Inputs(tables, source_stamps(ctx.engine, sources, ctx.config), utc_now())


__all__ = [
    "GATHERERS",
    "SCHEMAS",
    "Context",
    "Inputs",
    "clear_cache",
    "coverage",
    "data_version",
    "gather",
    "source_stamps",
]
