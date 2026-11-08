"""Which people and groups an opportunity fits, and why (INT-09).

An opportunity is read as concepts the way an expert search reads a question: the MeSH
headings (and their synonyms) its title and description name, and the headings of the
organization's papers its title finds. People are matched with the expert finder's scoring of
their concept profiles, which come from their accepted work; the headings that match are the
reasons shown. Fit is given in bands, never as a number to rank people by:

* **strong**: the person's work matches the opportunity's concepts closely;
* **some**: it matches part of them.

A group fits through its current members: as well as its best-fitting members do (the mean of
its top three), with those members as the reasons.

Fit says nothing about eligibility (see ``eligibility.py``). It is computed after each sync for
every open or forecast opportunity, into ``derived/``, and can be read again with concepts
added or removed on the opportunity's page.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import select
from sqlalchemy.engine import Engine

from tessera.analytics.concepts import profile_path
from tessera.analytics.experts import ConceptReader, _exists
from tessera.analytics.vocabulary import vocabulary_path
from tessera.evidence.memberships import is_current
from tessera.home import DataHome
from tessera.lake import Lake
from tessera.ledger.tables import assertion, unit
from tessera.opportunities import catalog
from tessera.search.index import index_path
from tessera.search.query import SearchIndex

log = logging.getLogger("tessera.opportunities")

FIT_FILE = "opportunity_fit.parquet"
PEOPLE_LIMIT = 25
GROUP_LIMIT = 10
STRONG, SOME = 0.60, 0.35  # a person's score: concepts matched, weighted (0 to about 1)
GROUP_TOP = 3
BANDS = {"strong": "Strong fit", "some": "Some fit"}

SCHEMA = pa.schema([
    ("opportunity", pa.string()), ("concepts", pa.string()), ("kind", pa.string()),
    ("id", pa.string()), ("score", pa.float64()), ("band", pa.string()),
    ("reasons", pa.string()),
])  # fmt: skip


def fit_path(home: DataHome) -> Path:
    return home.derived / FIT_FILE


def band(score: float) -> str | None:
    if score >= STRONG:
        return "strong"
    if score >= SOME:
        return "some"
    return None


# NIH titles end with the activity code and clinical-trial rule, which say nothing of the topic.
TRAILER = re.compile(r"\s*\([^()]*(?:Clinical Trial|[A-Z]\d\d)[^()]*\)\s*$")


def texts(o: dict[str, Any]) -> tuple[str, str]:
    """(everything the opportunity says, its title alone): the first is read for headings
    and synonyms, the second finds the organization's papers on the same subject."""
    title = TRAILER.sub("", o["title"] or "")
    body = " ".join(x for x in (title, o["description"]) if x)
    return body, title


def _memberships(engine: Engine, today: date) -> dict[str, list[str]]:
    """Each active group's current members."""
    out: dict[str, list[str]] = defaultdict(list)
    with engine.connect() as conn:
        active = {r[0] for r in conn.execute(select(unit.c.id).where(unit.c.status == "active"))}
        for r in conn.execute(
            select(assertion.c.subject_id, assertion.c.object_id, assertion.c.qualifiers).where(
                assertion.c.predicate == "member_of", assertion.c.status == "accepted")
        ).mappings():  # fmt: skip
            if r["object_id"] in active and is_current(r["qualifiers"] or {}, today):
                out[r["object_id"]].append(r["subject_id"])
    return out


def group_fits(people: list[dict[str, Any]], members: dict[str, list[str]]) -> list[dict[str, Any]]:
    """Groups that fit through their members, best first."""
    score_of = {p["id"]: p["score"] for p in people}
    out = []
    for group_id, ids in members.items():
        fitting = sorted((score_of[i], i) for i in set(ids) if i in score_of)[::-1]
        if not fitting:
            continue
        top = [s for s, _ in fitting[:GROUP_TOP]]
        score = round(sum(top) / GROUP_TOP, 4)
        found = band(score)
        if found:
            out.append({"id": group_id, "score": score, "band": found,
                        "members": [i for s, i in fitting if band(s)][:8]})  # fmt: skip
    out.sort(key=lambda g: (-g["score"], g["id"]))
    return out[:GROUP_LIMIT]


def read_one(
    reader: ConceptReader,
    index: SearchIndex,
    o: dict[str, Any],
    concepts: list[str] | None = None,
    drop: list[str] | None = None,
    cur: Any = None,
    source: str | None = None,
) -> dict[str, Any]:
    """One opportunity's concepts and the people who fit it (with ``concepts`` added and
    ``drop`` removed, as someone adjusting the reading asks)."""
    body, title = texts(o)
    wanted = reader.read(index, body, concepts or [], papers_by=f"{title} {body}", match="any")
    if drop:
        gone = {d.removeprefix("msh:") for d in drop}
        wanted = [c for c in wanted if c["descriptor_ui"] not in gone]
    people = []
    for p in reader.rank(wanted, PEOPLE_LIMIT, cur, source):
        found = band(p["score"])
        if found:
            people.append({**p, "band": found})
    return {"concepts": wanted, "people": people}


def build_fits(home: DataHome, engine: Engine, today: date | None = None) -> dict[str, Any]:
    """Read every open or forecast opportunity and write who fits it (``derived/``)."""
    today = today or date.today()
    started = time.monotonic()
    profile, path = profile_path(home), fit_path(home)
    if not _exists(profile) or not index_path(home).is_file():
        return {"opportunities": 0, "ready": False}
    members = _memberships(engine, today)
    rows: dict[str, list[Any]] = {name: [] for name in SCHEMA.names}

    def add(opp: str, concepts: str, kind: str, rid: str, score: float, found: str,
            reasons: Any) -> None:  # fmt: skip
        for name, value in zip(SCHEMA.names, (opp, concepts, kind, rid, score, found,
                                               json.dumps(reasons)), strict=True):  # fmt: skip
            rows[name].append(value)

    index = SearchIndex(index_path(home))
    count = 0
    with Lake(home.lake) as lake:
        reader = ConceptReader(lake, profile, vocabulary_path(home))
        views = [v for v in catalog.everything(engine, lake, today) if v["status"] in catalog.OPEN]
        cur = lake.cursor()
        try:
            cur.execute("CREATE OR REPLACE TEMP TABLE _profile AS SELECT * FROM read_parquet(?)",
                        [str(profile)])  # fmt: skip
            for o in sorted(views, key=lambda v: v["id"]):
                found = read_one(reader, index, o, cur=cur, source="_profile")
                concepts = json.dumps(found["concepts"])
                add(o["id"], concepts, "opportunity", o["id"], 0.0, "", [])
                for p in found["people"]:
                    add(o["id"], "", "person", p["id"], p["score"], p["band"], p["matches"])
                for g in group_fits(found["people"], members):
                    add(o["id"], "", "group", g["id"], g["score"], g["band"], g["members"])
                count += 1
        finally:
            cur.close()
    table = pa.table(rows, schema=SCHEMA)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".fit.", suffix=".parquet", dir=path.parent)
    os.close(fd)
    try:
        pq.write_table(table, tmp)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    seconds = round(time.monotonic() - started, 2)
    log.info("opportunity fit for %d opportunities in %.2fs", count, seconds)
    return {"opportunities": count, "rows": table.num_rows, "seconds": seconds, "ready": True}


# --- reading what was built ---------------------------------------------------------------------


def _rows(home: DataHome, where: str, params: list[Any]) -> list[dict[str, Any]]:
    path = fit_path(home)
    if not _exists(path):
        return []
    with Lake(home.lake) as lake:
        cur = lake.cursor()
        try:
            result = cur.execute(f"SELECT * FROM read_parquet(?) WHERE {where}",  # noqa: S608
                                 [str(path), *params])  # fmt: skip
            names = [d[0] for d in result.description]
            return [dict(zip(names, r, strict=True)) for r in result.fetchall()]
        finally:
            cur.close()


def _parsed(row: dict[str, Any]) -> dict[str, Any]:
    out = {"opportunity": row["opportunity"], "id": row["id"], "score": row["score"],
           "band": row["band"], "band_label": BANDS.get(row["band"], "")}  # fmt: skip
    reasons = json.loads(row["reasons"] or "[]")
    out["matches" if row["kind"] == "person" else "members"] = reasons
    return out


def fits_of(home: DataHome, ref: str) -> dict[str, Any] | None:
    """Who fits one opportunity (None when it was not read: closed, or nothing built yet)."""
    rows = _rows(home, "opportunity = ?", [ref])
    head = next((r for r in rows if r["kind"] == "opportunity"), None)
    if head is None:
        return None
    return {
        "concepts": json.loads(head["concepts"] or "[]"),
        "people": [_parsed(r) for r in rows if r["kind"] == "person"],
        "groups": [_parsed(r) for r in rows if r["kind"] == "group"],
    }


def fitting(home: DataHome, kind: str, rid: str) -> list[dict[str, Any]]:
    """The opportunities a person (``kind`` person) or a group fits, best first."""
    rows = _rows(home, "kind = ? AND id = ?", [kind, rid])
    return sorted((_parsed(r) for r in rows), key=lambda r: (-r["score"], r["opportunity"]))


def facets(home: DataHome) -> dict[str, list[tuple[str, str]]]:
    """For the search index: each opportunity's ``fits`` (people) and ``group`` facets."""
    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for r in _rows(home, "kind <> 'opportunity'", []):
        out[r["opportunity"]].append(("fits" if r["kind"] == "person" else "group", r["id"]))
    return out


__all__ = [
    "BANDS",
    "build_fits",
    "facets",
    "fit_path",
    "fits_of",
    "fitting",
    "group_fits",
    "read_one",
    "texts",
]
