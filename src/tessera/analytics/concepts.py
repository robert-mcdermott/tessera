"""Concept profiles and similar people (DIS-04, DIS-06).

A person's concept profile is a weighted vector over MeSH descriptors, from their papers:

    weight(p, c) = sum over p's papers with c of  status * major * position * recency * provisional
                   then * idf(c) = ln((N + 1) / people with c), then normalized to unit length

* **status:** 1 for a confirmed paper, ``discovery.likely_weight`` (0.5) for a strong
  suggestion not yet confirmed, so profiles work before curation catches up; 0 counts
  confirmed papers only. Weaker suggestions never count.
* **major:** 2 when the descriptor is a major topic of the paper; **position:** 1.5 for a
  first or last author; **recency:** halves every ``discovery.half_life_years`` (5).
* **provisional:** new papers have no MeSH for weeks; until they do, author keywords that
  are MeSH descriptor names stand in, at half weight, and are marked as provisional.
* Studies the person is an official of add their condition and intervention MeSH at half
  weight. Grant terms are machine-extracted and noisy, so they are not used.

Similarity between two people is the cosine of their profiles over each one's top concepts,
with the shared concepts that contribute most as the explanation. Pairs above a floor are
precomputed; nothing is computed when someone looks.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy.engine import Engine

from tessera.config import Config
from tessera.core.fsutil import fsync_dir
from tessera.evidence import store
from tessera.evidence.authorship import CHECK_TAGS
from tessera.home import DataHome
from tessera.lake import Lake

log = logging.getLogger("tessera.analytics")

PROFILE_FILE = "concept_profile.parquet"
SIMILARITY_FILE = "similarity_pair.parquet"
TOP_CONCEPTS = 200  # kept per person
# Laboratory tools that say how, not what: never a reason to call someone an expert. (Methods
# that are expertise in their own right, such as single-cell sequencing, stay.)
TOOLS = frozenset({
    "Green Fluorescent Proteins", "Luminescent Proteins", "Animals, Genetically Modified",
    "Tumor Cells, Cultured", "Transfection", "Plasmids", "Genetic Vectors", "Mutation",
    "Gene Knockdown Techniques", "RNA, Small Interfering", "Disease Models, Animal",
})  # fmt: skip
SIMILARITY_CONCEPTS = 60  # compared per person
SHARED_SHOWN = 5


def profile_path(home: DataHome) -> Path:
    return home.derived / PROFILE_FILE


def similarity_path(home: DataHome) -> Path:
    return home.derived / SIMILARITY_FILE


def _write(table: pa.Table, path: Path) -> None:
    """Atomically: readers see the old file or the new one, never a partial one."""
    tmp = path.with_suffix(".tmp.parquet")
    pq.write_table(table, tmp)
    with tmp.open("rb") as handle:
        os.fsync(handle.fileno())
    tmp.replace(path)
    fsync_dir(path.parent)


def linked_records(engine: Engine, config: Config) -> tuple[pa.Table, pa.Table, pa.Table]:
    """The papers, studies, and grants that count for each directory member.

    Papers and studies carry a status factor: 1 when confirmed, ``discovery.likely_weight``
    for a strong suggestion not yet confirmed. Grants come from the funder's investigator
    lists, so they are always confirmed."""
    likely = config.discovery.likely_weight
    strong = config.evidence.strong_threshold
    directory = {p.id for p in store.load_people(engine)}
    papers: dict[str, list[Any]] = {
        k: [] for k in ("person_id", "pmid", "position", "count", "factor", "confirmed")
    }
    studies: dict[str, list[Any]] = {k: [] for k in ("person_id", "nct_id", "factor")}
    grants: dict[str, list[Any]] = {"person_id": [], "core": []}
    for r in store.assertions_for(engine, statuses=("accepted", "suggested")):
        if r["subject_id"] not in directory:
            continue
        if r["predicate"] == "investigator_on":
            if r["status"] == "accepted":
                grants["person_id"].append(r["subject_id"])
                grants["core"].append(r["object_id"].removeprefix("grt:"))
            continue
        confirmed = r["status"] == "accepted"
        if not confirmed and (likely <= 0 or (r["score"] or 0) < strong):
            continue
        factor = 1.0 if confirmed else likely
        if r["predicate"] == "authored":
            q = r["qualifiers"] or {}
            papers["person_id"].append(r["subject_id"])
            papers["pmid"].append(r["object_id"].removeprefix("pub:"))
            papers["position"].append(q.get("author_position"))
            papers["count"].append(q.get("author_count"))
            papers["factor"].append(factor)
            papers["confirmed"].append(confirmed)
        elif r["predicate"] == "official_of":
            studies["person_id"].append(r["subject_id"])
            studies["nct_id"].append(r["object_id"].removeprefix("stu:"))
            studies["factor"].append(factor)
    return (
        pa.table(
            papers,
            schema=pa.schema(
                [
                    ("person_id", pa.string()),
                    ("pmid", pa.string()),
                    ("position", pa.int32()),
                    ("count", pa.int32()),
                    ("factor", pa.float64()),
                    ("confirmed", pa.bool_()),
                ]
            ),
        ),
        pa.table(
            studies,
            schema=pa.schema(
                [("person_id", pa.string()), ("nct_id", pa.string()), ("factor", pa.float64())]
            ),
        ),
        pa.table(grants, schema=pa.schema([("person_id", pa.string()), ("core", pa.string())])),
    )


PROFILE_SQL = """
WITH mesh AS (
  SELECT pmid, descriptor_ui AS ui, descriptor AS name, coalesce(major_topic, false) AS major
  FROM pubmed_mesh
  WHERE descriptor NOT IN (SELECT tag FROM _check_tags)
),
names AS (  -- descriptor names in the corpus, to read author keywords against
  SELECT lower(name) AS lname, min(ui) AS ui, min(name) AS name FROM mesh GROUP BY 1
),
unindexed AS (
  SELECT DISTINCT p.pmid FROM _papers p
  WHERE NOT EXISTS (SELECT 1 FROM pubmed_mesh m WHERE m.pmid = p.pmid)
),
provisional AS (
  SELECT DISTINCT a.pmid, n.ui, n.name, false AS major
  FROM pubmed_article a JOIN unindexed USING (pmid), unnest(a.keywords) AS k(kw)
  JOIN names n ON n.lname = lower(trim(k.kw))
),
terms AS (
  SELECT pmid, ui, name, major, false AS provisional FROM mesh
  UNION ALL SELECT pmid, ui, name, major, true FROM provisional
),
contrib AS (
  SELECT p.person_id, t.ui, t.name, t.provisional, p.pmid AS source, p.confirmed,
         p.factor
         * CASE WHEN t.major THEN 2.0 ELSE 1.0 END
         * CASE WHEN p.position = 1 OR p.position = p.count THEN 1.5 ELSE 1.0 END
         * CASE WHEN t.provisional THEN 0.5 ELSE 1.0 END
         * pow(0.5, greatest(0, ? - coalesce(a.pub_year, ?)) / ?) AS w
  FROM _papers p JOIN terms t USING (pmid) JOIN pubmed_article a USING (pmid)
  UNION ALL
  SELECT s.person_id, m.id, m.term, false, 'study:' || s.nct_id, s.factor = 1.0,
         0.5 * s.factor * pow(0.5, greatest(0, ? - coalesce(year(c.start_date), ?)) / ?)
  FROM _studies s JOIN ctgov_study c USING (nct_id),
       unnest(list_concat(coalesce(c.condition_mesh, []), coalesce(c.intervention_mesh, [])))
       AS x(m)
  WHERE m.id IS NOT NULL
),
agg AS (
  SELECT person_id, ui, min(name) AS name, sum(w) AS raw,
         count(DISTINCT source) AS papers,
         count(DISTINCT source) FILTER (WHERE confirmed) AS confirmed,
         bool_and(provisional) AS provisional
  FROM contrib GROUP BY person_id, ui
),
df AS (SELECT ui, count(*) AS people FROM agg GROUP BY ui),
population AS (SELECT count(DISTINCT person_id) AS n FROM agg),
weighted AS (
  SELECT agg.*, raw * ln((population.n + 1.0) / df.people) AS tfidf
  FROM agg JOIN df USING (ui), population
),
norms AS (SELECT person_id, sqrt(sum(tfidf * tfidf)) AS norm FROM weighted GROUP BY person_id)
SELECT person_id, ui AS descriptor_ui, name AS descriptor,
       tfidf / norm AS weight, raw, papers, confirmed, provisional
FROM weighted JOIN norms USING (person_id)
WHERE tfidf > 0 AND norm > 0
QUALIFY row_number() OVER (PARTITION BY person_id ORDER BY tfidf DESC, ui) <= ?
ORDER BY person_id, weight DESC
"""

SIMILARITY_SQL = """
WITH top AS (
  SELECT person_id, descriptor_ui AS ui, descriptor AS name, weight FROM _profile
  QUALIFY row_number() OVER (PARTITION BY person_id ORDER BY weight DESC, descriptor_ui) <= ?
),
shared AS (
  SELECT a.person_id AS a, b.person_id AS b, a.ui, a.name, a.weight * b.weight AS part
  FROM top a JOIN top b ON a.ui = b.ui AND a.person_id < b.person_id
),
pairs AS (
  SELECT a, b, sum(part) AS score,
         list_slice(
           list({'descriptor_ui': ui, 'descriptor': name, 'contribution': part}
                ORDER BY part DESC), 1, ?) AS shared
  FROM shared GROUP BY a, b HAVING sum(part) >= ?
),
both_ways AS (
  SELECT a AS person_id, b AS other_id, score, shared FROM pairs
  UNION ALL SELECT b, a, score, shared FROM pairs
)
SELECT * FROM both_ways
QUALIFY row_number() OVER (PARTITION BY person_id ORDER BY score DESC, other_id) <= ?
ORDER BY person_id, score DESC
"""


def build_profiles(
    config: Config, home: DataHome, engine: Engine, today: date | None = None
) -> dict[str, Any]:
    """Write ``derived/concept_profile.parquet`` and ``derived/similarity_pair.parquet``."""
    year = (today or date.today()).year
    half = config.discovery.half_life_years
    papers, studies, _ = linked_records(engine, config)
    with Lake(home.lake) as lake:
        cur = lake.cursor()
        try:
            cur.register("_papers", papers)
            cur.register("_studies", studies)
            cur.register("_check_tags", pa.table({"tag": sorted(CHECK_TAGS | TOOLS)}))
            profile = cur.sql(
                PROFILE_SQL, params=[year, year, half, year, year, half, TOP_CONCEPTS]
            ).to_arrow_table()
            cur.register("_profile", profile)
            similar = cur.sql(
                SIMILARITY_SQL,
                params=[
                    SIMILARITY_CONCEPTS,
                    SHARED_SHOWN,
                    config.discovery.similarity_floor,
                    config.discovery.similar_per_person,
                ],
            ).to_arrow_table()
        finally:
            cur.close()
    home.derived.mkdir(parents=True, exist_ok=True)
    _write(profile, profile_path(home))
    _write(similar, similarity_path(home))
    people = len(set(profile.column("person_id").to_pylist())) if profile.num_rows else 0
    log.info("concept profiles for %d people; %d similar pairs", people, similar.num_rows // 2)
    return {
        "people": people,
        "concepts": profile.num_rows,
        "pairs": similar.num_rows // 2,
        "papers": papers.num_rows,
        "confirmed_papers": sum(papers.column("confirmed").to_pylist()),
    }


__all__ = ["build_profiles", "linked_records", "profile_path", "similarity_path"]
