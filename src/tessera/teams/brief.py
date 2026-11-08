"""A team brief (INT-10): the team, its opportunity, each member's role with the confirmed work
that bears on the topics, and what is covered and missing, with the data's sources and dates.

Written as JSON (for the printable page), Markdown, or CSV (one row per member). Only confirmed
papers are listed: a brief goes to people outside Tessera, who cannot see the evidence.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any

import pyarrow as pa
from sqlalchemy import select
from sqlalchemy.engine import Engine

from tessera.config import Config
from tessera.core.clock import isoformat, utc_now
from tessera.home import DataHome
from tessera.insight.inputs import source_stamps
from tessera.lake import Lake
from tessera.ledger.tables import assertion
from tessera.opportunities import catalog
from tessera.teams.coverage import analyze
from tessera.teams.store import ROLE_LABELS

PAPERS = 5


def _links(engine: Engine, predicate: str, people: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {p: [] for p in people}
    with engine.connect() as conn:
        for subject, obj in conn.execute(
            select(assertion.c.subject_id, assertion.c.object_id).where(
                assertion.c.predicate == predicate, assertion.c.status == "accepted",
                assertion.c.reported.is_(True), assertion.c.subject_id.in_(people))
        ).all():  # fmt: skip
            out[subject].append(obj)
    return out


def _papers(lake: Lake, pmids: list[str], headings: set[str]) -> list[dict[str, Any]]:
    """A member's confirmed papers on the team's topics (newest first), or else their newest."""
    if not pmids:
        return []
    cur = lake.cursor()
    try:
        cur.register("_pmids", pa.table({"pmid": pa.array(sorted(set(pmids)), pa.string())}))
        rows = cur.execute(
            "SELECT a.pmid, a.title, a.journal, a.pub_year AS year, "
            "list(m.descriptor_ui) AS headings FROM pubmed_article a JOIN _pmids USING (pmid) "
            "LEFT JOIN pubmed_mesh m USING (pmid) WHERE NOT coalesce(a.is_retracted, false) "
            "GROUP BY 1, 2, 3, 4"
        ).fetchall()
    finally:
        cur.close()
    papers = [{"pmid": p, "title": t, "journal": j, "year": y,
               "on_topic": bool(headings & set(h or []))} for p, t, j, y, h in rows]  # fmt: skip
    papers.sort(key=lambda p: (not p["on_topic"], -(p["year"] or 0), p["pmid"]))
    return papers[:PAPERS]


def build(config: Config, home: DataHome, engine: Engine, found: dict[str, Any],
          today: date | None = None) -> dict[str, Any]:  # fmt: skip
    today = today or date.today()
    coverage = analyze(home, engine, found, today)
    members = [m["person_id"] for m in found["members"]]
    authored = {p: [r.removeprefix("pub:") for r in refs]
                for p, refs in _links(engine, "authored", members).items()}  # fmt: skip
    grants = _links(engine, "investigator_on", members)
    studies = _links(engine, "official_of", members)
    opportunity = None
    with Lake(home.lake) as lake:
        if found["opportunity"]:
            view = catalog.get(engine, lake, found["opportunity"], today)
            if view:
                opportunity = {k: view[k] for k in ("id", "title", "number", "sponsor", "status",
                                                     "status_label", "closes", "url")}  # fmt: skip
        cores = sorted({g.removeprefix("grt:") for refs in grants.values() for g in refs})
        active = {}
        if cores:
            marks = ", ".join("?" for _ in cores)
            for r in lake.rows(
                "SELECT core_project_num AS core, arg_max(title, fiscal_year) AS title, "  # noqa: S608
                "arg_max(activity_code, fiscal_year) AS activity, max(project_end) AS ends "
                f"FROM reporter_project WHERE core_project_num IN ({marks}) GROUP BY 1",
                cores,
            ):
                if r["ends"] is None or r["ends"] >= today:
                    active[r["core"]] = r
        ncts = sorted({s.removeprefix("stu:") for refs in studies.values() for s in refs})
        study_rows = {}
        if ncts:
            marks = ", ".join("?" for _ in ncts)
            study_rows = {r["nct_id"]: r for r in lake.rows(
                "SELECT nct_id, brief_title AS title, overall_status AS status "  # noqa: S608
                f"FROM ctgov_study WHERE nct_id IN ({marks})", ncts)}  # fmt: skip
        out_members = []
        for m in found["members"]:
            pid = m["person_id"]
            headings = {c["covered_by"][pid]["descriptor_ui"] for c in coverage["concepts"]
                        if pid in c["covered_by"]}  # fmt: skip
            out_members.append({
                "id": pid, "name": m["name"], "role": m["role"],
                "role_label": ROLE_LABELS.get(m["role"], m["role"]), "note": m["note"],
                "covers": [c["descriptor"] for c in coverage["concepts"] if pid in c["covered_by"]],
                "papers": _papers(lake, authored.get(pid, []), headings),
                "grants": [{"core": c, "title": active[c]["title"],
                            "activity": active[c]["activity"], "ends": active[c]["ends"]}
                           for c in sorted({g.removeprefix("grt:") for g in grants.get(pid, [])})
                           if c in active],
                "studies": [{"nct_id": n, "title": study_rows[n]["title"],
                             "status": study_rows[n]["status"]}
                            for n in sorted({s.removeprefix("stu:") for s in studies.get(pid, [])})
                            if n in study_rows],
            })  # fmt: skip
    names = {m["person_id"]: m["name"] for m in found["members"]}
    return {
        "team": {"id": found["id"], "name": found["name"], "purpose": found["purpose"],
                 "notes": found["notes"], "owner": found["owner_name"]},
        "opportunity": opportunity,
        "members": out_members,
        "coverage": [{"topic": c["descriptor"],
                      "covered_by": [names.get(p, p) for p in c["covered_by"]]}
                     for c in coverage["concepts"]],
        "missing": [c["descriptor"] for c in coverage["missing"]],
        "groups": [{"name": g["name"], "kind": g["kind"], "members": len(g["members"])}
                   for g in coverage["groups"]],
        "generated_at": isoformat(utc_now()),
        "organization": config.organization.name,
        "sources": source_stamps(engine, ["pubmed", "reporter", "ctgov"], config),
        "notes": ["Papers are confirmed links only. Grants are active NIH awards on which the "
                  "member is an investigator; studies are those the member is an official of, "
                  "with their registry status.", "Coverage reads each member's concept profile "
                  "from their confirmed work; it says what their papers are about, not what they "
                  "could do."],
    }  # fmt: skip


def markdown(b: dict[str, Any]) -> str:
    lines = [f"# {b['team']['name']}", ""]
    if b["team"]["purpose"]:
        lines += [b["team"]["purpose"], ""]
    o = b["opportunity"]
    if o:
        closes = f", closes {o['closes'].isoformat()}" if o["closes"] else ""
        lines += [f"**Opportunity:** {o['title']} ({o['number'] or o['id']}; {o['sponsor']}"
                  f"{closes}). {o['url'] or ''}".rstrip(), ""]  # fmt: skip
    lines += ["## Members", ""]
    for m in b["members"]:
        lines.append(f"### {m['name']}, {m['role_label'].lower()}")
        if m["note"]:
            lines.append(m["note"])
        if m["covers"]:
            lines.append(f"Covers: {', '.join(m['covers'])}.")
        if not m["papers"]:
            lines.append("- No confirmed papers yet.")
        for p in m["papers"]:
            venue = f"*{p['journal'] or ''}* {p['year'] or ''}".strip()
            lines.append(f"- {p['title']} {venue} (PMID {p['pmid']})")
        for g in m["grants"]:
            lines.append(f"- Grant {g['core']} ({g['activity']}): {g['title']}")
        for s in m["studies"]:
            lines.append(f"- Study {s['nct_id']}: {s['title']} (registry status: {s['status']})")
        lines.append("")
    lines += ["## Coverage", "", "| Topic | Covered by |", "|---|---|"]
    lines += [f"| {c['topic']} | {', '.join(c['covered_by']) or 'Nobody yet'} |"
              for c in b["coverage"]]  # fmt: skip
    lines.append("")
    if b["groups"]:
        lines += ["## Groups spanned", ""]
        lines += [f"- {g['name']} ({g['members']})" for g in b["groups"]]
        lines.append("")
    stamps = "; ".join(f"{s['label']} fetched {s['last_success_at'][:10]}"
                       for s in b["sources"] if s.get("last_success_at"))  # fmt: skip
    made = f"From Tessera at {b['organization']}, {b['generated_at'][:10]}. {stamps}."
    lines += ["---", "", *b["notes"], made]
    return "\n".join(lines) + "\n"


def members_csv(b: dict[str, Any]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["name", "role", "covers", "papers", "active_grants", "studies"])
    for m in b["members"]:
        writer.writerow([m["name"], m["role_label"], "; ".join(m["covers"]),
                         "; ".join(f"PMID {p['pmid']}" for p in m["papers"]),
                         "; ".join(g["core"] for g in m["grants"]),
                         "; ".join(s["nct_id"] for s in m["studies"])])  # fmt: skip
    return out.getvalue()


__all__ = ["build", "markdown", "members_csv"]
