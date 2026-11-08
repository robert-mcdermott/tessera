"""The full exit export (PLT-08): everything Tessera knows, in open formats, readable without it.

``tessera export`` writes a folder:

* ``ledger/<table>.parquet`` and ``.csv``: every ledger table (people, their names and
  identifiers, groups and memberships, every link with its evidence and decision, the decision
  logs, accounts without their password hashes, pages, reports, and settings);
* ``lake/<table>.parquet`` and ``.csv``: the current version of every record fetched from the
  public sources;
* ``snapshots/``: frozen report snapshots, as they are;
* ``DATA-DICTIONARY.md``: every table and column, with its type;
* ``README.md`` and ``manifest.json``: what is where, row counts, and digests.

Nested values (lists and objects) are JSON text in CSV files and native in Parquet.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import select
from sqlalchemy.engine import Engine

from tessera import __version__
from tessera.config import Config
from tessera.core.clock import isoformat, utc_now
from tessera.core.errors import TesseraError
from tessera.home import DataHome
from tessera.jobs.lock import job_lock
from tessera.lake import Lake
from tessera.lake.reader import quote_ident, quote_literal
from tessera.lake.schemas import DATASET_OF, TABLES
from tessera.ledger import migrate
from tessera.ledger.tables import metadata

# Never exported: secrets that would let someone sign in as somebody else.
WITHHELD = {"account": {"password_hash"}, "auth_session": {"token_hash", "csrf_token"}}
SKIPPED_TABLES = {"auth_session"}  # signed-in browsers: nothing anyone needs to leave with

LEDGER_NOTES = {
    "person": "One row per person in the directory, and discovered, dismissed, and merged ones",
    "person_name": "Name variants used for matching",
    "person_identifier": "ORCID iDs, NIH profile IDs, email addresses, and local IDs",
    "unit": "Groups: divisions, departments, programs, centers, labs, and other kinds",
    "unit_parent": "Which groups sit under which",
    "unit_event": "Every change to every group and membership",
    "assertion": "Every link (authorship, investigators, officials, memberships, "
    "attributions, acknowledgments), with its evidence, score, and decision",
    "curation_event": "Every decision about a link",
    "person_event": "Every decision about a person (promote, dismiss, identifiers, merges)",
    "person_merge": "Merges of duplicate people, with what each moved",
    "account": "Accounts (without their password hashes)",
    "role_grant": "Accounts' roles and their scopes",
    "group_page": "Each group's draft and published page",
    "group_page_revision": "Every published version of each group's page",
    "opening": "Groups' openings",
    "theme": "Groups' themes and projects",
    "theme_link": "What each theme or project links",
    "group_inbox": "When each group's inbox was last reviewed",
    "cohort": "Saved Insight cohorts",
    "saved_search": "People's saved searches",
    "report": "Insight reports",
    "report_snapshot": "Frozen report snapshots (their files are in snapshots/)",
    "opportunity": "Funding opportunities the organization tracks itself (foundation, internal)",
    "auto_accept_batch": "Links accepted automatically, by batch",
    "ingest_run": "Every fetch, resolve, and build run",
    "source_checkpoint": "Where each source's incremental fetches left off",
    "setting": "Settings changed in the app (the evidence model, auto-accept, the last backup)",
}
LAKE_NOTES = {
    "pubmed_article": "PubMed records",
    "pubmed_author": "Authors of each PubMed record, in order, with affiliations",
    "pubmed_mesh": "MeSH headings of each PubMed record",
    "pubmed_grant": "Grants each PubMed record acknowledges",
    "citing_article": "PubMed records of clinical articles that cite the organization's papers",
    "icite_metrics": "NIH iCite citation metrics, by PubMed ID",
    "icite_clinical_citation": "Clinical articles NIH iCite lists as citing each paper",
    "grants_gov_opportunity": "Federal funding opportunities from Grants.gov, open and closed",
    "reporter_project": "NIH RePORTER fiscal-year award records",
    "reporter_investigator": "Investigators on each RePORTER record",
    "reporter_publication_link": "Publications RePORTER links to each grant",
    "ctgov_study": "ClinicalTrials.gov registry records",
    "ctgov_official": "Officials of each study",
    "ctgov_site": "Sites of each study",
    "ctgov_reference": "Publications each study cites",
    "ctgov_secondary_id": "Other identifiers of each study, including grants",
    "orcid_record": "ORCID records",
    "orcid_work": "Works on ORCID records",
    "orcid_employment": "Employment on ORCID records",
    "mesh_descriptor": "NLM's MeSH descriptors: names, tree numbers, and synonyms",
}


class ExitError(TesseraError):
    """An exit export that cannot be written."""


@dataclass
class ExitResult:
    out: Path
    ledger: dict[str, int]
    lake: dict[str, int]
    files: int
    bytes: int


def _value(value: Any) -> Any:
    if isinstance(value, datetime):
        return isoformat(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _csv_cell(value: Any) -> Any:
    value = _value(value)
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, default=str)
    return "" if value is None else value


def _write_csv(path: Path, header: list[str], rows: Any) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row in rows:
            writer.writerow([_csv_cell(v) for v in row])


def _ledger_table(names: list[str], rows: list[dict[str, Any]]) -> pa.Table:
    if not rows:
        return pa.table({n: pa.array([], pa.string()) for n in names})
    data = {
        n: [json.dumps(r[n], ensure_ascii=False, default=str) if isinstance(r[n], dict | list)
            else r[n] for r in rows]
        for n in names
    }  # fmt: skip
    return pa.table(data)


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_exit(config: Config, home: DataHome, engine: Engine, out: Path) -> ExitResult:
    if out.exists() and any(out.iterdir()):
        raise ExitError(f"{out} is not empty; choose a new folder.")
    if not migrate.is_current(engine):
        raise ExitError("The ledger schema is not current; run `tessera db upgrade` first.")
    out.mkdir(parents=True, exist_ok=True)
    (out / "ledger").mkdir()
    (out / "lake").mkdir()
    dictionary: list[str] = []
    ledger_counts: dict[str, int] = {}
    lake_counts: dict[str, int] = {}
    with job_lock(home.root):
        with engine.connect() as conn:
            for table in metadata.sorted_tables:
                if table.name in SKIPPED_TABLES:
                    continue
                hidden = WITHHELD.get(table.name, set())
                columns = [c for c in table.columns if c.name not in hidden]
                key = list(table.primary_key.columns)
                rows = [
                    {c.name: _value(r[c.name]) for c in columns}
                    for r in conn.execute(select(*columns).order_by(*key)).mappings()
                ]
                ledger_counts[table.name] = len(rows)
                names = [c.name for c in columns]
                _write_csv(out / "ledger" / f"{table.name}.csv", names,
                           ([row[n] for n in names] for row in rows))  # fmt: skip
                pq.write_table(_ledger_table(names, rows), out / "ledger" / f"{table.name}.parquet")
                dictionary += _ledger_entry(table, columns, len(rows), hidden)
        with Lake(home.lake) as lake:
            cur = lake.cursor()
            try:
                for name, spec in TABLES.items():
                    target = out / "lake" / f"{name}.parquet"
                    cur.execute(
                        f"COPY (SELECT * EXCLUDE (_content_hash) FROM {quote_ident(name)}) "  # noqa: S608
                        f"TO {quote_literal(str(target))} (FORMAT parquet)"
                    )
                    table = pq.read_table(target)
                    lake_counts[name] = table.num_rows
                    _write_csv(out / "lake" / f"{name}.csv", table.column_names,
                               (list(r.values()) for r in table.to_pylist()))  # fmt: skip
                    dictionary += _lake_entry(name, spec, table)
            finally:
                cur.close()
        snapshots = home.root / "snapshots"
        if snapshots.is_dir() and any(snapshots.iterdir()):
            shutil.copytree(snapshots, out / "snapshots",
                            ignore=shutil.ignore_patterns(".*"))  # fmt: skip
    (out / "DATA-DICTIONARY.md").write_text(_dictionary(dictionary), "utf-8")
    (out / "README.md").write_text(_readme(config, ledger_counts, lake_counts), "utf-8")
    files = {p.relative_to(out).as_posix(): {"bytes": p.stat().st_size, "sha256": _hash(p)}
             for p in sorted(out.rglob("*")) if p.is_file()}  # fmt: skip
    manifest = {
        "format": "tessera-exit/1",
        "tessera_version": __version__,
        "created_at": isoformat(utc_now()),
        "organization": config.organization.name,
        "ledger_revision": migrate.current_revision(engine),
        "ledger": ledger_counts,
        "lake": lake_counts,
        "files": files,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), "utf-8")
    total = sum(f["bytes"] for f in files.values())
    return ExitResult(out, ledger_counts, lake_counts, len(files) + 1, total)


def _ledger_entry(table: Any, columns: list[Any], rows: int, hidden: set[str]) -> list[str]:
    note = LEDGER_NOTES.get(table.name, "")
    lines = [f"### ledger/{table.name}", "", f"{note} ({rows:,} rows)."]
    if hidden:
        lines.append(f"Left out: {', '.join(sorted(hidden))}.")
    lines += ["", "| Column | Type | Empty allowed |", "|---|---|---|"]
    for c in columns:
        lines.append(f"| `{c.name}` | {str(c.type).lower()} | {'yes' if c.nullable else 'no'} |")
    return [*lines, ""]


def _lake_entry(name: str, spec: Any, table: pa.Table) -> list[str]:
    dataset = DATASET_OF[name].name
    lines = [
        f"### lake/{name}",
        "",
        f"{LAKE_NOTES.get(name, '')} (from `{dataset}`, {table.num_rows:,} rows; key: "
        f"{', '.join(spec.key)}). Columns starting with `_` record the fetch run and when.",
        "",
        "| Column | Type |",
        "|---|---|",
    ]
    lines += [f"| `{f.name}` | {f.type} |" for f in table.schema]
    return [*lines, ""]


def _dictionary(entries: list[str]) -> str:
    return "\n".join(
        [
            "# Data dictionary",
            "",
            "Every table in this export, with its columns. Ledger tables hold what people "
            "decided and own; lake tables hold the current version of each record fetched "
            "from public sources. Times are UTC, in ISO 8601.",
            "",
            *entries,
        ]
    )


def _readme(config: Config, ledger: dict[str, int], lake: dict[str, int]) -> str:
    return f"""# {config.organization.name}: a full export from Tessera

Written {isoformat(utc_now())} by Tessera {__version__}. Everything here is in open formats and
can be read without Tessera: Parquet with DuckDB, pandas, Polars, R, or Spark; CSV with anything.

- `ledger/`: {len(ledger)} tables of what people decided and own: people and their names and
  identifiers, groups and memberships, every link with its evidence and decision, the decision
  logs, accounts (without password hashes), group pages, reports, and settings.
  {sum(ledger.values()):,} rows.
- `lake/`: {len(lake)} tables, the current version of every record fetched from PubMed, iCite,
  NIH RePORTER, ClinicalTrials.gov, ORCID, and MeSH. {sum(lake.values()):,} rows.
- `snapshots/`: frozen Insight report snapshots, if any, each with its inputs, figures, and
  manifest.
- `DATA-DICTIONARY.md`: every table and column.
- `manifest.json`: row counts, and the size and SHA-256 digest of every file.

In CSV files, lists and objects (evidence, qualifiers, author lists) are JSON text. Links
(`ledger/assertion`) name their subject and object as `per_...` (people), `unt_...` (groups),
`pub:<PubMed ID>`, `grt:<core project number>`, and `stu:<NCT number>`.

Not included, because they are computed from the above: the search index, the discovery
files, and the public site. The raw responses from each source stay in the data home's `raw/`.
"""


__all__ = ["ExitError", "ExitResult", "write_exit"]
