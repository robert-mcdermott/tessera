"""Reports and snapshots (INT-03).

A **report** is a named set of figures over one cohort and one period, shared with everyone
who can read Insight. Opening it computes the figures from the data as it is now.

A **snapshot** freezes a report: it computes every figure once and keeps, in
``snapshots/<id>/`` in the data home, everything needed to compute them again exactly:

* ``inputs/<table>.parquet``: the tables the figures were computed from (the cohort's members
  with their years, the links, and the rows read from the lake);
* ``figures/<n>-<metric>.json``: each figure as it was shown, with its records;
* ``manifest.json``: the report's definition, the resolved cohort, each figure's metric,
  version, period, and options, the sources with their data timestamps, the moment it was
  computed, and a SHA-256 digest of every input file and every figure.

``verify`` reads the inputs back, computes each figure again with the same metric versions,
and compares the digests. A later sync changes live reports, never snapshots.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.engine import Connection, Engine

from tessera import __version__
from tessera.config import Config
from tessera.connectors.reporter import fiscal_year
from tessera.core.clock import isoformat, utc_now
from tessera.core.errors import TesseraError
from tessera.core.ids import new_id
from tessera.home import DataHome
from tessera.insight import cohorts, metrics
from tessera.insight.figure import Figure, Period
from tessera.insight.inputs import Context, Inputs, gather
from tessera.insight.run import compute, earliest
from tessera.lake import Lake
from tessera.ledger.tables import report, report_snapshot

MAX_FIGURES = 24
SNAPSHOTS = "snapshots"


class ReportError(TesseraError):
    """A report or snapshot that cannot be made, found, or trusted."""


def canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str).encode("utf-8")  # fmt: skip


def digest(payload: Any) -> str:
    return hashlib.sha256(canonical(payload)).hexdigest()


def clean_figures(figures: Any) -> list[dict[str, Any]]:
    if not isinstance(figures, list) or not figures:
        raise ReportError("A report has at least one figure.")
    if len(figures) > MAX_FIGURES:
        raise ReportError(f"A report has at most {MAX_FIGURES} figures.")
    out = []
    for n, item in enumerate(figures, start=1):
        if isinstance(item, str):
            item = {"metric": item}
        if not isinstance(item, dict) or not isinstance(item.get("metric"), str):
            raise ReportError(f"Figure {n} names no metric.")
        try:
            metric = metrics.get(item["metric"])
            options = metric.resolve_options(item.get("options") or {})
        except metrics.MetricError as exc:
            raise ReportError(f"Figure {n}: {exc}") from exc
        out.append({"metric": metric.id, "options": options})
    return out


def _clean(
    conn: Connection, name: str, cohort: str, start: int | None, end: int | None,
    exclude: str | None = None,
) -> tuple[str, str]:  # fmt: skip
    name = (name or "").strip()
    if not name or len(name) > 200:
        raise ReportError("A report's name has 1 to 200 characters.")
    clash = select(report.c.id).where(report.c.name == name)
    if exclude:
        clash = clash.where(report.c.id != exclude)
    if conn.execute(clash).first() is not None:
        raise ReportError(f"There is already a report called {name!r}.")
    cohort = (cohort or "organization").strip()
    found = cohorts.parse(cohort)
    if isinstance(found, str):
        cohorts.get_cohort(conn, found)
    Period.make(start, end)  # the years must make a period
    return name, cohort


def create_report(
    conn: Connection, *, name: str, cohort: str, figures: Any, actor: str,
    description: str | None = None, start: int | None = None, end: int | None = None,
    owner: str | None = None,
) -> str:  # fmt: skip
    name, cohort = _clean(conn, name, cohort, start, end)
    report_id = new_id("rpt")
    now = utc_now()
    conn.execute(
        insert(report).values(
            id=report_id, name=name, description=(description or "").strip() or None,
            cohort=cohort, start_year=start, end_year=end, figures=clean_figures(figures),
            created_by=actor, created_at=now, updated_by=actor, updated_at=now, owner=owner,
        )
    )  # fmt: skip
    return report_id


def get_report(conn: Connection, report_id: str) -> dict[str, Any]:
    row = conn.execute(select(report).where(report.c.id == report_id)).mappings().first()
    if row is None:
        raise ReportError(f"No report {report_id!r}.")
    return dict(row)


def find_report(conn: Connection, ref: str) -> dict[str, Any]:
    """A report by its ID or its name."""
    row = conn.execute(
        select(report).where((report.c.id == ref) | (report.c.name == ref))
    ).mappings().first()  # fmt: skip
    if row is None:
        raise ReportError(f"No report {ref!r}.")
    return dict(row)


def list_reports(conn: Connection) -> list[dict[str, Any]]:
    counts = dict(
        conn.execute(
            select(report_snapshot.c.report_id, func.count()).group_by(report_snapshot.c.report_id)
        ).all()
    )
    rows = conn.execute(select(report).order_by(report.c.name)).mappings()
    return [{**dict(r), "snapshots": counts.get(r["id"], 0)} for r in rows]


def update_report(
    conn: Connection, report_id: str, *, actor: str, name: str | None = None,
    description: str | None = None, cohort: str | None = None, figures: Any = None,
    start: int | str | None = "keep", end: int | str | None = "keep",
) -> None:  # fmt: skip
    row = get_report(conn, report_id)
    new_start = row["start_year"] if start == "keep" else start
    new_end = row["end_year"] if end == "keep" else end
    clean_name, clean_cohort = _clean(
        conn, name if name is not None else row["name"],
        cohort if cohort is not None else row["cohort"],
        new_start, new_end, exclude=report_id,  # type: ignore[arg-type]
    )  # fmt: skip
    values: dict[str, Any] = {
        "name": clean_name, "cohort": clean_cohort, "start_year": new_start,
        "end_year": new_end, "updated_by": actor, "updated_at": utc_now(),
    }  # fmt: skip
    if description is not None:
        values["description"] = description.strip() or None
    if figures is not None:
        values["figures"] = clean_figures(figures)
    conn.execute(update(report).where(report.c.id == report_id).values(**values))


def delete_report(conn: Connection, report_id: str) -> None:
    """Delete a report. Its snapshots stay, detached, because someone may cite them."""
    get_report(conn, report_id)
    conn.execute(delete(report).where(report.c.id == report_id))


# --- computing ---------------------------------------------------------------------------------


def _period(metric: metrics.Metric, row: dict[str, Any], inputs: Inputs, today: date) -> Period:
    basis = "fiscal" if metric.period == "fiscal" else "calendar"
    end = row["end_year"]
    if end is None and basis == "fiscal":
        end = fiscal_year(today)
    first = earliest(inputs.sources_for(metric.sources))
    return Period.make(row["start_year"], end, basis, today=today, earliest=first)


def compute_report(
    engine: Engine, lake: Lake, config: Config, row: dict[str, Any], *,
    today: date | None = None, search: Any = None,
) -> tuple[cohorts.Cohort, Inputs, list[Figure]]:  # fmt: skip
    """Resolve the report's cohort once, gather every input once, and compute each figure."""
    today = today or date.today()
    cohort = cohorts.resolve(engine, row["cohort"], today=today, search=search)
    chosen = [(metrics.get(f["metric"]), f["options"]) for f in row["figures"]]
    needs: list[str] = []
    for metric, _ in chosen:
        needs += [n for n in metric.needs if n not in needs]
    inputs = gather(Context(engine, lake, config, cohort), needs)
    generated_at = utc_now()
    figures = [
        compute(
            metric, inputs, cohort.to_json(), _period(metric, row, inputs, today),
            metric.resolve_options(options), small_number=config.insight.small_number,
            today=today, generated_at=generated_at,
        )
        for metric, options in chosen
    ]  # fmt: skip
    return cohort, inputs, figures


# --- snapshots ---------------------------------------------------------------------------------


def snapshot_dir(home: DataHome, snapshot_id: str) -> Path:
    return home.root / SNAPSHOTS / snapshot_id


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def take_snapshot(
    engine: Engine, lake: Lake, config: Config, home: DataHome, report_id: str, *,
    actor: str, note: str | None = None, today: date | None = None, search: Any = None,
) -> str:  # fmt: skip
    today = today or date.today()
    with engine.connect() as conn:
        row = get_report(conn, report_id)
    cohort, inputs, figures = compute_report(engine, lake, config, row, today=today,
                                             search=search)  # fmt: skip
    snapshot_id = new_id("snp")
    final = snapshot_dir(home, snapshot_id)
    work = final.with_name(f".{snapshot_id}.partial")
    (work / "inputs").mkdir(parents=True)
    (work / "figures").mkdir()
    try:
        files: dict[str, dict[str, Any]] = {}
        for name, table in sorted(inputs.tables.items()):
            path = work / "inputs" / f"{name}.parquet"
            pq.write_table(table, path)
            files[name] = {"rows": table.num_rows, "sha256": _file_hash(path)}
        entries = []
        for n, fig in enumerate(figures, start=1):
            payload = fig.to_json(records=True)
            file = f"{n:02d}-{fig.metric.id}.json"
            (work / "figures" / file).write_bytes(canonical(payload))
            entries.append(
                {
                    "metric": fig.metric.id, "version": fig.metric.version,
                    "ref": fig.metric.ref, "name": fig.metric.name,
                    "options": fig.request.options, "period": fig.request.period.to_json(),
                    "file": file, "digest": digest(payload),
                }
            )  # fmt: skip
        taken_at = utc_now()
        manifest = {
            "snapshot": snapshot_id,
            "report": {"id": row["id"], "name": row["name"], "description": row["description"],
                       "cohort": row["cohort"], "start": row["start_year"],
                       "end": row["end_year"]},
            "cohort": cohort.to_json(),
            "taken_at": isoformat(taken_at),
            "taken_by": actor,
            "note": (note or "").strip() or None,
            "today": today.isoformat(),
            "generated_at": isoformat(figures[0].request.generated_at) if figures else None,
            "small_number": config.insight.small_number,
            "tessera_version": __version__,
            "sources": inputs.sources,
            "gathered_at": isoformat(inputs.gathered_at),
            "inputs": files,
            "figures": entries,
        }  # fmt: skip
        manifest["digest"] = digest({"inputs": files, "figures": [e["digest"] for e in entries]})
        (work / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), "utf-8"
        )
        final.parent.mkdir(parents=True, exist_ok=True)
        work.replace(final)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    with engine.begin() as conn:
        conn.execute(
            insert(report_snapshot).values(
                id=snapshot_id, report_id=row["id"], name=row["name"], note=manifest["note"],
                taken_by=actor, taken_at=taken_at, digest=manifest["digest"],
                path=f"{SNAPSHOTS}/{snapshot_id}",
                summary={
                    "cohort": {"label": cohort.label, "size": cohort.size,
                               "spec": cohort.to_json()["spec"]},
                    "figures": [e["ref"] for e in entries],
                    "periods": sorted({e["period"]["label"] for e in entries}),
                },
            )
        )  # fmt: skip
    return snapshot_id


def get_snapshot(conn: Connection, snapshot_id: str) -> dict[str, Any]:
    row = conn.execute(
        select(report_snapshot).where(report_snapshot.c.id == snapshot_id)
    ).mappings().first()  # fmt: skip
    if row is None:
        raise ReportError(f"No snapshot {snapshot_id!r}.")
    return dict(row)


def list_snapshots(conn: Connection, report_id: str | None = None) -> list[dict[str, Any]]:
    query = select(report_snapshot).order_by(report_snapshot.c.taken_at.desc())
    if report_id:
        query = query.where(report_snapshot.c.report_id == report_id)
    return [dict(r) for r in conn.execute(query).mappings()]


def _folder(home: DataHome, row: dict[str, Any]) -> Path:
    folder = (home.root / row["path"]).resolve()
    if not folder.is_relative_to((home.root / SNAPSHOTS).resolve()) or not folder.is_dir():
        raise ReportError(f"Snapshot {row['id']}'s files are missing from the data home.")
    return folder


def load_snapshot(home: DataHome, row: dict[str, Any]) -> tuple[dict[str, Any], list[dict]]:
    """The manifest and every figure as it was frozen."""
    folder = _folder(home, row)
    manifest = json.loads((folder / "manifest.json").read_text("utf-8"))
    figures = [json.loads((folder / "figures" / e["file"]).read_text("utf-8"))
               for e in manifest["figures"]]  # fmt: skip
    return manifest, figures


def verify_snapshot(home: DataHome, row: dict[str, Any]) -> dict[str, Any]:
    """Compute every figure again from the frozen inputs and compare digests."""
    folder = _folder(home, row)
    manifest = json.loads((folder / "manifest.json").read_text("utf-8"))
    problems: list[str] = []
    tables = {}
    for name, info in manifest["inputs"].items():
        path = folder / "inputs" / f"{name}.parquet"
        if not path.is_file():
            problems.append(f"Input {name} is missing.")
            continue
        if _file_hash(path) != info["sha256"]:
            problems.append(f"Input {name} has changed since the snapshot was taken.")
        tables[name] = pq.read_table(path)
    inputs = Inputs(tables, manifest["sources"], datetime.fromisoformat(manifest["gathered_at"]))
    today = date.fromisoformat(manifest["today"])
    generated = (
        datetime.fromisoformat(manifest["generated_at"]) if manifest["generated_at"] else None
    )
    results = []
    for entry in manifest["figures"]:
        outcome = {"metric": entry["ref"], "name": entry["name"], "expected": entry["digest"]}
        try:
            metric = metrics.get(entry["metric"])
        except metrics.MetricError:
            outcome |= {"same": False, "problem": "This metric no longer exists."}
            results.append(outcome)
            continue
        if metric.version != entry["version"]:
            outcome |= {"same": False, "problem": f"The metric is now version {metric.version}; "
                        f"the snapshot used version {entry['version']}."}  # fmt: skip
            results.append(outcome)
            continue
        if problems:
            outcome |= {"same": False, "problem": "The inputs are not as they were."}
            results.append(outcome)
            continue
        again = compute(
            metric, inputs, manifest["cohort"], Period.from_json(entry["period"]),
            entry["options"], small_number=manifest["small_number"], today=today,
            generated_at=generated,
        ).to_json(records=True)  # fmt: skip
        actual = digest(again)
        outcome |= {"actual": actual, "same": actual == entry["digest"]}
        results.append(outcome)
    same = not problems and all(r["same"] for r in results)
    return {"snapshot": row["id"], "same": same, "problems": problems, "figures": results}


__all__ = [
    "ReportError",
    "canonical",
    "clean_figures",
    "compute_report",
    "create_report",
    "delete_report",
    "digest",
    "find_report",
    "get_report",
    "get_snapshot",
    "list_reports",
    "list_snapshots",
    "load_snapshot",
    "take_snapshot",
    "update_report",
    "verify_snapshot",
]
