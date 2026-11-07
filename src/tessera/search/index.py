"""Build the derived search and serving index (``derived/search.db``).

One SQLite file with:

* ``doc``: one row per organization-relevant person, publication, grant, and study, with
  display fields and a JSON summary, so lists render without touching the lake;
* ``doc_fts``: an FTS5 full-text index over titles, people, terms, and body text;
* ``facet``: (doc, name, value) rows for faceted filtering and counts.

The lake holds everything fetched, including papers pulled in by name searches that turn
out to be other people's. The index holds only what is relevant to the organization:
members' papers (accepted, or suggested at or above the review threshold), papers with an
author affiliated with the organization, papers acknowledging grants, and papers cited
by studies. It is rebuilt atomically and can always be regenerated (ADR-0007).
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
from sqlalchemy import select
from sqlalchemy.engine import Engine

from tessera.config import Config
from tessera.core.clock import isoformat, utc_now
from tessera.core.fsutil import fsync_dir
from tessera.evidence import store
from tessera.evidence.authorship import CHECK_TAGS
from tessera.evidence.orgmatch import OrgMatcher
from tessera.home import DataHome
from tessera.lake import Lake
from tessera.ledger.tables import unit

log = logging.getLogger("tessera.search")

INDEX_NAME = "search.db"
SCHEMA_VERSION = 1
KINDS = ("person", "publication", "grant", "study")

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE doc (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    ref TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    subtitle TEXT,
    year INTEGER,
    sort_key TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 0,
    summary TEXT NOT NULL
);
CREATE INDEX ix_doc_kind_sort ON doc (kind, sort_key);
CREATE INDEX ix_doc_kind_year ON doc (kind, year);
CREATE TABLE facet (doc_id INTEGER NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL);
CREATE INDEX ix_facet_name_value ON facet (name, value, doc_id);
CREATE INDEX ix_facet_doc ON facet (doc_id);
CREATE VIRTUAL TABLE doc_fts USING fts5(
    title, people, terms, body, tokenize = 'porter unicode61 remove_diacritics 2'
);
"""


def index_path(home: DataHome) -> Path:
    return home.derived / INDEX_NAME


@dataclass
class Doc:
    kind: str
    ref: str
    title: str
    subtitle: str | None
    year: int | None
    sort_key: str
    weight: float
    summary: dict[str, Any]
    people: str = ""
    terms: str = ""
    body: str = ""
    facets: list[tuple[str, str]] | None = None


def _authors_display(authors: list[tuple[int, str | None, str | None, str | None]]) -> str:
    names = []
    for _, family, initials, collective in sorted(authors):
        names.append(f"{family} {initials or ''}".strip() if family else (collective or ""))
    names = [n for n in names if n]
    return ", ".join(names[:6]) + (", et al." if len(names) > 6 else "")


def _pmids_table(pmids: set[str]) -> pa.Table:
    return pa.table({"pmid": pa.array(sorted(pmids), pa.string())})


class _Builder:
    def __init__(self, config: Config, home: DataHome, engine: Engine, lake: Lake) -> None:
        self.config, self.home, self.engine, self.lake = config, home, engine, lake
        self.matcher = OrgMatcher(config.organization)
        self.review = config.evidence.review_threshold
        self.strong = config.evidence.strong_threshold
        self.docs: list[Doc] = []

    # -- ledger -----------------------------------------------------------------------------

    def load_ledger(self) -> None:
        people = store.load_people(self.engine, ("active", "former"))
        self.people = {p.id: p for p in people}
        self.assertions: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in store.assertions_for(self.engine):  # reported only
            if row["status"] == "rejected":
                continue
            self.assertions[row["predicate"]].append(row)
        with self.engine.connect() as conn:
            self.units = {r.id: r.name for r in conn.execute(select(unit.c.id, unit.c.name))}

    def member_links(self, predicate: str) -> list[dict[str, Any]]:
        """Links from people in the directory that count (accepted, or likely enough)."""
        return [
            r
            for r in self.assertions[predicate]
            if r["subject_id"] in self.people
            and (r["status"] == "accepted" or (r["score"] is None or r["score"] >= self.review))
        ]

    # -- publications -----------------------------------------------------------------------

    def publications(self) -> None:
        authored = self.member_links("authored")
        relevant = {r["object_id"].removeprefix("pub:") for r in authored}
        relevant |= {r["subject_id"].removeprefix("pub:") for r in self.assertions["acknowledges"]}
        relevant |= {r["object_id"].removeprefix("pub:") for r in self.assertions["references"]}
        for row in self.lake.rows(
            "SELECT DISTINCT pmid, affiliations FROM pubmed_author WHERE len(affiliations) > 0"
        ):
            if row["pmid"] not in relevant and self.matcher.match_any(row["affiliations"]):
                relevant.add(row["pmid"])
        self.relevant_pmids = relevant
        if not relevant:
            return
        members: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in authored:
            person = self.people[r["subject_id"]]
            members[r["object_id"].removeprefix("pub:")].append(
                {
                    "id": person.id,
                    "name": person.display_name,
                    "status": r["status"],
                    "score": r["score"],
                    "position": (r["qualifiers"] or {}).get("author_position"),
                }
            )
        cur = self.lake.cursor()
        try:
            cur.register("_pmids", _pmids_table(relevant))
            articles = cur.execute(
                "SELECT a.pmid, a.title, a.abstract, coalesce(a.journal_abbrev, a.journal), "
                "a.journal, a.pub_year, a.pub_date, a.doi, a.is_retracted, a.keywords, "
                "a.publication_types, m.citation_count, m.relative_citation_ratio "
                "FROM pubmed_article a JOIN _pmids USING (pmid) "
                "LEFT JOIN icite_metrics m USING (pmid)"
            ).fetchall()
            authors: dict[str, list[tuple]] = defaultdict(list)
            author_names: dict[str, list[str]] = defaultdict(list)
            for pmid, pos, family, given, initials, collective in cur.execute(
                "SELECT pmid, position, family, given, initials, collective_name "
                "FROM pubmed_author JOIN _pmids USING (pmid)"
            ).fetchall():
                authors[pmid].append((pos, family, initials, collective))
                author_names[pmid].append(" ".join(p for p in (given, family, collective) if p))
            mesh: dict[str, list[str]] = defaultdict(list)
            for pmid, descriptor in cur.execute(
                "SELECT pmid, descriptor FROM pubmed_mesh JOIN _pmids USING (pmid)"
            ).fetchall():
                mesh[pmid].append(descriptor)
        finally:
            cur.close()
        for (
            pmid,
            title,
            abstract,
            journal,
            journal_full,
            year,
            pub_date,
            doi,
            retracted,
            keywords,
            types,
            citations,
            rcr,
        ) in articles:
            count = len(authors[pmid])
            summary = {
                "pmid": pmid,
                "title": title or "(untitled)",
                "journal": journal,
                "year": year,
                "pub_date": pub_date.isoformat() if pub_date else None,
                "authors": _authors_display(authors[pmid]),
                "author_count": count,
                "doi": doi,
                "is_retracted": bool(retracted),
                "citation_count": citations,
                "relative_citation_ratio": round(rcr, 2) if rcr is not None else None,
                "members": sorted(members.get(pmid, []), key=lambda m: m["position"] or 0),
            }
            facets = [("type", t) for t in (types or [])]
            if year:
                facets.append(("year", str(year)))
            if journal:
                facets.append(("journal", journal))
            if retracted:
                facets.append(("flag", "retracted"))
            for member in summary["members"]:
                facets.append(("person", member["id"]))
            self.docs.append(
                Doc(
                    "publication",
                    f"pub:{pmid}",
                    title or "(untitled)",
                    journal,
                    year,
                    sort_key=f"{9999 - (year or 0):04d}{pmid}",
                    weight=float(citations or 0),
                    summary=summary,
                    people="; ".join(author_names[pmid]),
                    terms="; ".join([*mesh[pmid], *(keywords or []), journal_full or ""]),
                    body=abstract or "",
                    facets=facets,
                )
            )

    # -- grants -----------------------------------------------------------------------------

    def grants(self) -> None:
        investigators: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in self.member_links("investigator_on"):
            person = self.people[r["subject_id"]]
            investigators[r["object_id"].removeprefix("grt:")].append(
                {
                    "id": person.id,
                    "name": person.display_name,
                    "role": (r["qualifiers"] or {}).get("role"),
                }
            )
        rows = self.lake.rows(
            "SELECT core_project_num, fiscal_year, title, abstract, activity_code, "
            "agency_ic_admin, org_name, award_amount, subproject_id, project_start, project_end, "
            "is_active, terms FROM reporter_project WHERE core_project_num IS NOT NULL "
            "ORDER BY core_project_num, fiscal_year"
        )
        pis: dict[str, set[str]] = defaultdict(set)
        for r in self.lake.rows(
            "SELECT p.core_project_num, i.full_name FROM reporter_investigator i "
            "JOIN reporter_project p USING (appl_id)"
        ):
            if r["full_name"]:
                pis[r["core_project_num"]].add(r["full_name"])
        by_core: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            by_core[r["core_project_num"]].append(r)
        papers = Counter(
            r["object_id"].removeprefix("grt:") for r in self.assertions["acknowledges"]
        )
        for core, records in by_core.items():
            ours = core in investigators or any(
                self.matcher.matches(r["org_name"]) for r in records
            )
            if not ours:
                continue
            latest = records[-1]
            years = sorted({r["fiscal_year"] for r in records if r["fiscal_year"]})
            parent = [r for r in records if not r["subproject_id"]] or records
            total = sum(r["award_amount"] or 0 for r in parent)
            active = bool(latest["is_active"])
            summary = {
                "core_project_num": core,
                "title": latest["title"] or core,
                "activity_code": latest["activity_code"],
                "institute": latest["agency_ic_admin"],
                "organization": latest["org_name"],
                "fiscal_years": [years[0], years[-1]] if years else None,
                "latest_award": latest["award_amount"],
                "total_awarded": total,
                "amount_note": "Sum of total-cost awards across fiscal-year records in the data "
                "(subprojects excluded)",
                "project_start": latest["project_start"].isoformat()
                if latest["project_start"]
                else None,
                "project_end": latest["project_end"].isoformat() if latest["project_end"] else None,
                "is_active": active,
                "investigators": investigators.get(core, []),
                "publication_count": papers.get(core, 0),
            }
            facets = [("status", "active" if active else "ended")]
            for key, value in (
                ("activity", latest["activity_code"]),
                ("institute", latest["agency_ic_admin"]),
            ):
                if value:
                    facets.append((key, value))
            if years:
                facets.append(("year", str(years[-1])))
            for inv in summary["investigators"]:
                facets.append(("person", inv["id"]))
            self.docs.append(
                Doc(
                    "grant",
                    f"grt:{core}",
                    latest["title"] or core,
                    f"{core} · {latest['agency_ic_admin'] or ''}".strip(" ·"),
                    years[-1] if years else None,
                    sort_key=f"{9999 - (years[-1] if years else 0):04d}{core}",
                    weight=float(total),
                    summary=summary,
                    people="; ".join(sorted(pis[core])),
                    terms="; ".join(
                        [core, latest["activity_code"] or "", *(latest["terms"] or [])]
                    ),
                    body=latest["abstract"] or "",
                    facets=facets,
                )
            )

    # -- studies ----------------------------------------------------------------------------

    def studies(self) -> None:
        officials: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in self.member_links("official_of"):
            person = self.people[r["subject_id"]]
            officials[r["object_id"].removeprefix("stu:")].append(
                {
                    "id": person.id,
                    "name": person.display_name,
                    "status": r["status"],
                    "score": r["score"],
                    "role": (r["qualifiers"] or {}).get("role"),
                }
            )
        names: dict[str, list[str]] = defaultdict(list)
        for r in self.lake.rows("SELECT nct_id, name FROM ctgov_official"):
            if r["name"]:
                names[r["nct_id"]].append(r["name"])
        for r in self.lake.rows(
            "SELECT nct_id, brief_title, official_title, overall_status, phases, study_type, "
            "conditions, keywords, interventions, lead_sponsor, start_date, brief_summary, "
            "condition_mesh FROM ctgov_study"
        ):
            nct = r["nct_id"]
            year = r["start_date"].year if r["start_date"] else None
            summary = {
                "nct_id": nct,
                "title": r["brief_title"] or nct,
                "registry_status": r["overall_status"],
                "phases": r["phases"] or [],
                "study_type": r["study_type"],
                "conditions": (r["conditions"] or [])[:5],
                "lead_sponsor": r["lead_sponsor"],
                "start_date": r["start_date"].isoformat() if r["start_date"] else None,
                "officials": officials.get(nct, []),
            }
            facets = [("phase", p) for p in r["phases"] or []]
            for key, value in (("status", r["overall_status"]), ("type", r["study_type"])):
                if value:
                    facets.append((key, value))
            if year:
                facets.append(("year", str(year)))
            for official in summary["officials"]:
                facets.append(("person", official["id"]))
            interventions = [i["name"] for i in r["interventions"] or [] if i.get("name")]
            mesh = [m["term"] for m in r["condition_mesh"] or [] if m.get("term")]
            self.docs.append(
                Doc(
                    "study",
                    f"stu:{nct}",
                    r["brief_title"] or nct,
                    f"{nct} · {r['lead_sponsor'] or ''}".strip(" ·"),
                    year,
                    sort_key=f"{9999 - (year or 0):04d}{nct}",
                    weight=0.0,
                    summary=summary,
                    people="; ".join(names[nct]),
                    terms="; ".join(
                        [
                            *(r["conditions"] or []),
                            *interventions,
                            *mesh,
                            *(r["keywords"] or []),
                            r["lead_sponsor"] or "",
                            nct,
                        ]
                    ),
                    body=" ".join(p for p in (r["official_title"], r["brief_summary"]) if p),
                    facets=facets,
                )
            )

    # -- people -----------------------------------------------------------------------------

    def persons(self) -> None:
        papers: dict[str, dict[str, int]] = defaultdict(lambda: {"accepted": 0, "suggested": 0})
        likely: dict[str, set[str]] = defaultdict(set)
        for r in self.member_links("authored"):
            bucket = "accepted" if r["status"] == "accepted" else "suggested"
            papers[r["subject_id"]][bucket] += 1
            if r["status"] == "accepted" or (r["score"] or 0) >= self.strong:
                likely[r["subject_id"]].add(r["object_id"].removeprefix("pub:"))
        grants = Counter(r["subject_id"] for r in self.member_links("investigator_on"))
        studies = Counter(r["subject_id"] for r in self.member_links("official_of"))
        memberships: dict[str, list[str]] = defaultdict(list)
        for r in self.assertions["member_of"]:
            if r["object_id"] in self.units:
                memberships[r["subject_id"]].append(self.units[r["object_id"]])
        concepts: dict[str, Counter] = defaultdict(Counter)
        all_likely = set().union(*likely.values()) if likely else set()
        if all_likely:
            by_pmid: dict[str, list[str]] = defaultdict(list)
            cur = self.lake.cursor()
            try:
                cur.register("_pmids", _pmids_table(all_likely))
                for pmid, descriptor in cur.execute(
                    "SELECT pmid, descriptor FROM pubmed_mesh JOIN _pmids USING (pmid)"
                ).fetchall():
                    if descriptor not in CHECK_TAGS:
                        by_pmid[pmid].append(descriptor)
            finally:
                cur.close()
            for person_id, pmids in likely.items():
                for pmid in pmids:
                    concepts[person_id].update(by_pmid.get(pmid, []))
        # Rank each person's topics by distinctiveness (TF-IDF), so topics everyone shares
        # ("Signal Transduction") give way to what sets this person apart.
        spread = Counter(c for counter in concepts.values() for c in counter)
        population = max(1, len(concepts))

        def distinctive(person_id: str) -> list[str]:
            counter = concepts[person_id]
            ranked = sorted(
                counter,
                key=lambda c: (-counter[c] * math.log((population + 1) / (spread[c] + 0.5)), c),
            )
            return ranked[:8]

        for person in self.people.values():
            top = distinctive(person.id)
            primary = person.names[0] if person.names else None
            summary = {
                "id": person.id,
                "name": person.display_name,
                "status": person.status,
                "units": sorted(memberships.get(person.id, [])),
                "orcid": next(iter(sorted(person.ids("orcid"))), None),
                "counts": {
                    **papers[person.id],
                    "grants": grants[person.id],
                    "studies": studies[person.id],
                },
                "concepts": top,
            }
            facets = [("status", person.status), *(("unit", u) for u in summary["units"])]
            sort_key = (
                f"{primary.family_key if primary else person.display_name.lower()}"
                f" {person.display_name.lower()}"
            )
            self.docs.append(
                Doc(
                    "person",
                    person.id,
                    person.display_name,
                    ", ".join(summary["units"]) or None,
                    None,
                    sort_key=sort_key,
                    weight=float(papers[person.id]["accepted"] + papers[person.id]["suggested"]),
                    summary=summary,
                    people="; ".join(sorted({n.display for n in person.names})),
                    terms="; ".join([*summary["units"], *top]),
                    facets=facets,
                )
            )

    # -- write ------------------------------------------------------------------------------

    def write(self, path: Path) -> dict[str, int]:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".search.", suffix=".db", dir=path.parent)
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            con = sqlite3.connect(tmp)
            con.executescript(SCHEMA)
            for doc_id, doc in enumerate(self.docs, start=1):
                con.execute(
                    "INSERT INTO doc VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        doc_id,
                        doc.kind,
                        doc.ref,
                        doc.title,
                        doc.subtitle,
                        doc.year,
                        doc.sort_key,
                        doc.weight,
                        json.dumps(doc.summary, default=str),
                    ),
                )
                con.execute(
                    "INSERT INTO doc_fts (rowid, title, people, terms, body) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (doc_id, doc.title, doc.people, doc.terms, doc.body),
                )
                if doc.facets:
                    con.executemany(
                        "INSERT INTO facet VALUES (?, ?, ?)",
                        [(doc_id, name, value) for name, value in dict.fromkeys(doc.facets)],
                    )
            counts = Counter(d.kind for d in self.docs)
            con.executemany(
                "INSERT INTO meta VALUES (?, ?)",
                [
                    ("schema_version", str(SCHEMA_VERSION)),
                    ("built_at", isoformat(utc_now())),
                    ("organization", self.config.organization.name),
                    *((f"count:{k}", str(counts.get(k, 0))) for k in KINDS),
                ],
            )
            con.execute("INSERT INTO doc_fts (doc_fts) VALUES ('optimize')")
            con.commit()
            con.execute("VACUUM")
            con.close()
            tmp.replace(path)
            fsync_dir(path.parent)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return {k: counts.get(k, 0) for k in KINDS}


def build_index(config: Config, home: DataHome, engine: Engine) -> dict[str, Any]:
    """Rebuild ``derived/search.db`` from the lake and ledger. Returns counts and timing."""
    home.require()
    started = time.monotonic()
    with Lake(home.lake) as lake:
        builder = _Builder(config, home, engine, lake)
        builder.load_ledger()
        builder.publications()
        builder.grants()
        builder.studies()
        builder.persons()
        counts = builder.write(index_path(home))
    elapsed = round(time.monotonic() - started, 2)
    log.info("search index built in %.2fs: %s", elapsed, counts)
    return {"documents": counts, "seconds": elapsed}
