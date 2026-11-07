"""Read the lake with DuckDB.

Each process opens its own in-memory DuckDB with read-only views over the Parquet files
listed in each table's manifest. No DuckDB database file exists, so no process ever
holds a lock that would block ingestion or other readers.

For every table there are two views:

* ``<table>__all``: every row ever written (all versions).
* ``<table>``: the current version only. For a root table, the latest row per key;
  for a child table, the rows belonging to the current version of their root record.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa

from tessera.lake.manifest import Manifest, read_manifest, table_dir
from tessera.lake.schemas import DATASET_OF, DATASETS, TABLES, DatasetSpec, TableSpec

# Parquet support is built into DuckDB; nothing may be downloaded at runtime (ADR-0003).
DUCKDB_CONFIG = {"autoinstall_known_extensions": False, "autoload_known_extensions": False}


def connect_duckdb() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(":memory:", config=dict(DUCKDB_CONFIG))


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def files_sql(paths: Sequence[Path]) -> str:
    """A ``read_parquet`` expression over the given files."""
    listing = ", ".join(quote_literal(str(p)) for p in paths)
    return f"read_parquet([{listing}], union_by_name = true)"


def current_root_sql(spec: TableSpec, source: str) -> str:
    keys = ", ".join(quote_ident(k) for k in spec.key)
    return (
        f"SELECT * FROM {source} QUALIFY row_number() OVER ("
        f"PARTITION BY {keys} ORDER BY _retrieved_at DESC, _run_id DESC) = 1"
    )


def current_child_sql(dataset: DatasetSpec, child: TableSpec, source: str, root: str) -> str:
    join = " AND ".join(f"r.{quote_ident(k)} = c.{quote_ident(k)}" for k in dataset.group_key)
    return (
        f"SELECT c.* FROM {source} AS c WHERE EXISTS (SELECT 1 FROM {root} AS r WHERE {join} "
        "AND r._run_id = c._run_id AND r._retrieved_at = c._retrieved_at)"
    )


class Lake:
    """Read access to the lake for one process. Thread-safe via per-call cursors."""

    def __init__(self, lake_root: Path, *, refresh_interval: float = 1.0) -> None:
        self.root = lake_root
        self.refresh_interval = refresh_interval
        self._con = connect_duckdb()
        self._lock = threading.RLock()
        self._generations: dict[str, int] = {}
        self._manifests: dict[str, Manifest] = {}
        self._last_check = 0.0
        self.refresh(force=True)

    # -- views ------------------------------------------------------------------------

    def refresh(self, *, force: bool = False) -> bool:
        """Re-read manifests and rebuild views for tables that changed.

        Cheap when nothing changed; throttled to once per ``refresh_interval`` seconds
        unless ``force`` is set. Returns True if any view was rebuilt.
        """
        now = time.monotonic()
        if not force and now - self._last_check < self.refresh_interval:
            return False
        with self._lock:
            self._last_check = now
            changed = False
            for dataset in DATASETS.values():
                manifests = {
                    t.name: read_manifest(self.root, t.name, t.version) for t in dataset.tables
                }
                if not force and all(
                    self._generations.get(name) == m.generation for name, m in manifests.items()
                ):
                    continue
                self._build_dataset_views(dataset, manifests)
                changed = True
            return changed

    def _build_dataset_views(self, dataset: DatasetSpec, manifests: dict[str, Manifest]) -> None:
        for spec in dataset.tables:  # root first: child views depend on the root view
            manifest = manifests[spec.name]
            paths = [table_dir(self.root, spec.name) / entry.path for entry in manifest.files]
            all_view = quote_ident(f"{spec.name}__all")
            if paths:
                source = files_sql(paths)
            else:
                # A real (empty) table, not a registered Arrow object: registrations are
                # local to one connection, while tables are visible to every cursor.
                empty = quote_ident(f"_empty_{spec.name}")
                self._con.register("_empty_arrow", pa.Table.from_pylist([], schema=spec.schema))
                self._con.execute(f"CREATE OR REPLACE TABLE {empty} AS SELECT * FROM _empty_arrow")
                self._con.unregister("_empty_arrow")
                source = empty
            self._con.execute(f"CREATE OR REPLACE VIEW {all_view} AS SELECT * FROM {source}")
            if spec is dataset.root:
                current = current_root_sql(spec, all_view)
            else:
                current = current_child_sql(dataset, spec, all_view, quote_ident(dataset.root.name))
            self._con.execute(f"CREATE OR REPLACE VIEW {quote_ident(spec.name)} AS {current}")
            self._generations[spec.name] = manifest.generation
            self._manifests[spec.name] = manifest

    # -- queries ----------------------------------------------------------------------

    def cursor(self) -> duckdb.DuckDBPyConnection:
        """A connection for the calling thread (shares the views)."""
        self.refresh()
        return self._con.cursor()

    def arrow(self, sql: str, params: Sequence[Any] | None = None) -> pa.Table:
        cur = self.cursor()
        try:
            return cur.sql(sql, params=list(params) if params else None).to_arrow_table()
        finally:
            cur.close()

    def rows(self, sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        return self.arrow(sql, params).to_pylist()

    def scalar(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        cur = self.cursor()
        try:
            row = cur.execute(sql, params or []).fetchone()
            return None if row is None else row[0]
        finally:
            cur.close()

    def current_hashes(self, dataset: DatasetSpec, keys: Iterable[tuple[Any, ...]]) -> dict:
        """Map each given root key (as a tuple) to the content hash of its current row."""
        key_rows = [dict(zip(dataset.group_key, key, strict=True)) for key in keys]
        if not key_rows:
            return {}
        root = dataset.root
        lookup = pa.Table.from_pylist(
            key_rows, schema=pa.schema([root.schema.field(k) for k in dataset.group_key])
        )
        join = " AND ".join(f"r.{quote_ident(k)} = k.{quote_ident(k)}" for k in dataset.group_key)
        select = ", ".join(f"r.{quote_ident(k)}" for k in dataset.group_key)
        cur = self.cursor()
        try:
            cur.register("_keys", lookup)
            result = cur.execute(
                f"SELECT {select}, r._content_hash FROM {quote_ident(root.name)} AS r "
                f"JOIN _keys AS k ON {join}"
            ).fetchall()
        finally:
            cur.close()
        return {tuple(row[:-1]): row[-1] for row in result}

    def stats(self) -> list[dict[str, Any]]:
        """Per-table file and row counts for status displays."""
        self.refresh(force=True)
        out = []
        for name, spec in TABLES.items():
            manifest = self._manifests[name]
            current = self.scalar(f"SELECT count(*) FROM {quote_ident(name)}")
            out.append(
                {
                    "table": name,
                    "dataset": DATASET_OF[name].name,
                    "files": len(manifest.files),
                    "rows_written": manifest.rows,
                    "rows_current": current,
                    "generation": manifest.generation,
                    "updated_at": manifest.updated_at,
                    "schema_version": spec.version,
                }
            )
        return out

    def close(self) -> None:
        with self._lock:
            self._con.close()

    def __enter__(self) -> Lake:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
