"""Compaction and garbage collection for lake tables.

Compaction rewrites a dataset's tables so that each holds only its current rows, in a
few key-sorted files, and swaps the manifests. Superseded files stay on disk until
``gc`` removes them after a retention period, so a reader that still has the old file
list keeps working until its next refresh.
"""

from __future__ import annotations

import contextlib
import shutil
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from tessera.core.clock import isoformat, utc_now
from tessera.core.fsutil import fsync_dir, fsync_file
from tessera.core.ids import new_id
from tessera.lake.manifest import (
    FileEntry,
    Manifest,
    read_manifest,
    table_dir,
    table_lock,
    write_manifest,
)
from tessera.lake.reader import (
    connect_duckdb,
    current_child_sql,
    current_root_sql,
    files_sql,
    quote_ident,
)
from tessera.lake.schemas import DATASETS, DatasetSpec
from tessera.lake.writer import DATA, STAGING

DEFAULT_RETENTION = timedelta(days=7)
ROWS_PER_FILE = 1_000_000


def _paths(lake_root: Path, manifest: Manifest) -> list[Path]:
    return [table_dir(lake_root, manifest.table) / e.path for e in manifest.files]


def compact_dataset(lake_root: Path, dataset: DatasetSpec) -> dict[str, Any]:
    """Compact every table of a dataset. Returns per-table before/after counts."""
    run_id = new_id("run")
    snapshot = {t.name: read_manifest(lake_root, t.name, t.version) for t in dataset.tables}
    if all(len(m.files) <= 1 for m in snapshot.values()):
        return {"dataset": dataset.name, "skipped": "already compact", "tables": {}}

    con = connect_duckdb()
    report: dict[str, Any] = {"dataset": dataset.name, "run_id": run_id, "tables": {}}
    try:
        root = dataset.root
        root_files = _paths(lake_root, snapshot[root.name])
        if root_files:
            con.execute(f"CREATE VIEW _root_all AS SELECT * FROM {files_sql(root_files)}")
            con.execute(f"CREATE VIEW _root AS {current_root_sql(root, '_root_all')}")
        # Children first, root last, mirroring the writer's publish order.
        for spec in (*dataset.children, root):
            manifest = snapshot[spec.name]
            if not manifest.files:
                continue
            if spec is root:
                query = "SELECT * FROM _root"
            else:
                source = f"(SELECT * FROM {files_sql(_paths(lake_root, manifest))})"
                query = current_child_sql(dataset, spec, source, "_root") if root_files else None
            keys = ", ".join(quote_ident(k) for k in spec.key)
            table = con.sql(f"{query} ORDER BY {keys}").to_arrow_table() if query else None
            new_entries = []
            data_dir = table_dir(lake_root, spec.name) / DATA
            data_dir.mkdir(parents=True, exist_ok=True)
            if table is not None and table.num_rows:
                table = table.cast(spec.schema) if table.schema != spec.schema else table
                for index, start in enumerate(range(0, table.num_rows, ROWS_PER_FILE), 1):
                    chunk = table.slice(start, ROWS_PER_FILE)
                    target = data_dir / f"{run_id}-c{index:03d}.parquet"
                    pq.write_table(chunk, target, compression="zstd")
                    fsync_file(target)
                    new_entries.append(
                        FileEntry(
                            path=f"{DATA}/{target.name}",
                            run_id=run_id,
                            rows=chunk.num_rows,
                            added_at=isoformat(utc_now()),
                        )
                    )
                fsync_dir(data_dir)
            with table_lock(lake_root, spec.name):
                latest = read_manifest(lake_root, spec.name, spec.version)
                compacted = {e.path for e in manifest.files}
                arrived_since = [e for e in latest.files if e.path not in compacted]
                now = isoformat(utc_now())
                latest.superseded.extend({"path": p, "at": now} for p in sorted(compacted))
                latest.files = new_entries + arrived_since
                write_manifest(lake_root, latest)
            report["tables"][spec.name] = {
                "files_before": len(manifest.files),
                "rows_before": manifest.rows,
                "files_after": len(new_entries) + len(arrived_since),
                "rows_after": sum(e.rows for e in new_entries) + sum(e.rows for e in arrived_since),
            }
    finally:
        con.close()
    return report


def compact_all(lake_root: Path, datasets: list[str] | None = None) -> list[dict[str, Any]]:
    names = datasets or list(DATASETS)
    return [compact_dataset(lake_root, DATASETS[name]) for name in names]


def gc(lake_root: Path, retention: timedelta = DEFAULT_RETENTION) -> dict[str, int]:
    """Delete superseded files and abandoned staging folders older than ``retention``."""
    cutoff = utc_now() - retention
    removed_files = 0
    removed_staging = 0
    for dataset in DATASETS.values():
        for spec in dataset.tables:
            directory = table_dir(lake_root, spec.name)
            if not directory.is_dir():
                continue
            with table_lock(lake_root, spec.name):
                manifest = read_manifest(lake_root, spec.name, spec.version)
                keep = []
                for item in manifest.superseded:
                    if item["at"] < isoformat(cutoff):
                        with contextlib.suppress(FileNotFoundError):
                            (directory / item["path"]).unlink()
                            removed_files += 1
                    else:
                        keep.append(item)
                if len(keep) != len(manifest.superseded):
                    manifest.superseded = keep
                    write_manifest(lake_root, manifest)
            staging = directory / STAGING
            if staging.is_dir():
                for run_dir in staging.iterdir():
                    age = time.time() - run_dir.stat().st_mtime
                    if age > retention.total_seconds():
                        shutil.rmtree(run_dir, ignore_errors=True)
                        removed_staging += 1
    return {"files": removed_files, "staging_folders": removed_staging}
