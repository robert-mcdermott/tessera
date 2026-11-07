"""Write records into the lake, crash-safely.

Usage::

    with LakeWriter(home.lake, PUBMED, run_id) as writer:
        writer.add(records)          # dicts: root fields plus child lists
    # committed here; on an exception nothing becomes visible

Files are first written to ``<table>/_staging/<run_id>/``. On commit they are renamed
into ``<table>/data/`` and the manifests are swapped, children before the root. Child
rows only count as current when their root row is current, so a crash between the
child and root swaps leaves the previous data visible and intact.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from tessera.core.clock import isoformat, to_utc, utc_now
from tessera.core.errors import LakeError
from tessera.core.fsutil import fsync_dir, fsync_file
from tessera.lake.manifest import FileEntry, read_manifest, table_dir, table_lock, write_manifest
from tessera.lake.reader import Lake
from tessera.lake.schemas import DatasetSpec, TableSpec

STAGING = "_staging"
DATA = "data"


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return isoformat(value)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"cannot hash value of type {type(value).__name__}")


def content_hash(row: Mapping[str, Any]) -> str:
    """Stable hash of a row's content (key order and bookkeeping fields ignored)."""
    payload = {k: v for k, v in row.items() if not k.startswith("_")}
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_json_default)
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()


@dataclass
class WriteStats:
    received: int = 0
    unchanged: int = 0
    written: dict[str, int] = field(default_factory=dict)

    @property
    def changed(self) -> int:
        return self.received - self.unchanged


class LakeWriter:
    def __init__(
        self,
        lake_root: Path,
        dataset: DatasetSpec,
        run_id: str,
        *,
        source_version: str | None = None,
        skip_unchanged: bool = True,
        flush_rows: int = 50_000,
    ) -> None:
        self.root = lake_root
        self.dataset = dataset
        self.run_id = run_id
        self.source_version = source_version
        self.skip_unchanged = skip_unchanged
        self.flush_rows = flush_rows
        self.stats = WriteStats()
        self._buffers: dict[str, list[dict[str, Any]]] = {t.name: [] for t in dataset.tables}
        self._parts: dict[str, list[tuple[Path, int]]] = {t.name: [] for t in dataset.tables}
        self._run_hashes: dict[tuple[Any, ...], str] = {}
        self._last_retrieved: datetime | None = None
        self._reader: Lake | None = None
        self._closed = False

    # -- public API -------------------------------------------------------------------

    def add(
        self, records: Iterable[Mapping[str, Any]], retrieved_at: datetime | None = None
    ) -> int:
        """Buffer records (root fields plus child lists). Returns how many were new or changed."""
        if self._closed:
            raise LakeError("writer already committed or aborted")
        stamp = self._next_stamp(retrieved_at)
        batch: dict[tuple[Any, ...], tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]] = {}
        for record in records:
            root_row, children = self._split(record)
            key = tuple(root_row[k] for k in self.dataset.group_key)
            batch[key] = (root_row, children)  # a later duplicate in one call wins
        self.stats.received += len(batch)

        hashes = {key: self._bundle_hash(root, kids) for key, (root, kids) in batch.items()}
        existing = self._existing_hashes(list(batch)) if self.skip_unchanged else {}
        accepted = 0
        for key, (root_row, children) in batch.items():
            digest = hashes[key]
            previous = self._run_hashes.get(key, existing.get(key))
            if self.skip_unchanged and previous == digest:
                self.stats.unchanged += 1
                continue
            self._run_hashes[key] = digest
            self._append(self.dataset.root, root_row, stamp, digest)
            for child in self.dataset.children:
                for row in children[child.name]:
                    self._append(child, row, stamp, content_hash(row))
            accepted += 1
        if len(self._buffers[self.dataset.root.name]) >= self.flush_rows:
            self._flush()
        return accepted

    def commit(self) -> WriteStats:
        """Publish everything written by this writer."""
        if self._closed:
            raise LakeError("writer already committed or aborted")
        self._flush()
        # Children first, root last: see the module docstring.
        for spec in (*self.dataset.children, self.dataset.root):
            parts = self._parts[spec.name]
            if not parts:
                continue
            data_dir = table_dir(self.root, spec.name) / DATA
            data_dir.mkdir(parents=True, exist_ok=True)
            with table_lock(self.root, spec.name):
                manifest = read_manifest(self.root, spec.name, spec.version)
                added_at = isoformat(utc_now())
                for staged, rows in parts:
                    target = data_dir / f"{self.run_id}-{staged.stem.split('-')[-1]}.parquet"
                    staged.replace(target)
                    manifest.files.append(
                        FileEntry(
                            path=f"{DATA}/{target.name}",
                            run_id=self.run_id,
                            rows=rows,
                            added_at=added_at,
                        )
                    )
                fsync_dir(data_dir)
                write_manifest(self.root, manifest)
            self.stats.written[spec.name] = sum(rows for _, rows in parts)
        self._finish()
        return self.stats

    def abort(self) -> None:
        """Discard everything staged by this writer."""
        self._finish()

    def __enter__(self) -> LakeWriter:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._closed:
            return
        if exc_type is None:
            self.commit()
        else:
            self.abort()

    # -- internals --------------------------------------------------------------------

    def _next_stamp(self, retrieved_at: datetime | None) -> datetime:
        stamp = to_utc(retrieved_at) if retrieved_at else utc_now()
        if self._last_retrieved is not None and stamp <= self._last_retrieved:
            stamp = self._last_retrieved + timedelta(microseconds=1)
        self._last_retrieved = stamp
        return stamp

    def _split(self, record: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, list]]:
        root = self.dataset.root
        child_fields = {c.field: c for c in self.dataset.children}
        allowed = set(root.column_names) | set(child_fields)
        unknown = set(record) - allowed
        if unknown:
            raise LakeError(f"{root.name}: unknown fields {sorted(unknown)}")
        root_row = {name: record.get(name) for name in root.column_names}
        missing = [k for k in root.key if root_row[k] is None]
        if missing:
            raise LakeError(f"{root.name}: record is missing key fields {missing}")
        children: dict[str, list[dict[str, Any]]] = {}
        for name, child in child_fields.items():
            rows = []
            for item in record.get(name) or []:
                extra = set(item) - set(child.column_names)
                if extra:
                    raise LakeError(f"{child.name}: unknown fields {sorted(extra)}")
                row = {col: item.get(col) for col in child.column_names}
                for k in self.dataset.group_key:  # children inherit the root key
                    row[k] = root_row[k]
                rows.append(row)
            children[child.name] = rows
        return root_row, children

    def _bundle_hash(self, root_row: dict[str, Any], children: dict[str, list]) -> str:
        parts = [content_hash(root_row)]
        for name in sorted(children):
            parts.append(name + ":" + ",".join(sorted(content_hash(r) for r in children[name])))
        return hashlib.blake2b("|".join(parts).encode(), digest_size=16).hexdigest()

    def _existing_hashes(self, keys: list[tuple[Any, ...]]) -> dict[tuple[Any, ...], str]:
        if self._reader is None:
            self._reader = Lake(self.root, refresh_interval=3600)
        return self._reader.current_hashes(self.dataset, keys)

    def _append(self, spec: TableSpec, row: dict[str, Any], stamp: datetime, digest: str) -> None:
        row = dict(row)
        row["_run_id"] = self.run_id
        row["_retrieved_at"] = stamp
        row["_content_hash"] = digest
        row["_source_version"] = self.source_version
        self._buffers[spec.name].append(row)

    def _flush(self) -> None:
        for spec in self.dataset.tables:
            rows = self._buffers[spec.name]
            if not rows:
                continue
            try:
                table = pa.Table.from_pylist(rows, schema=spec.schema)
            except (pa.ArrowInvalid, pa.ArrowTypeError, TypeError, ValueError) as exc:
                raise LakeError(f"{spec.name}: rows do not match the table schema: {exc}") from exc
            sort_keys = [(k, "ascending") for k in spec.key]
            table = table.sort_by(sort_keys)
            staging = table_dir(self.root, spec.name) / STAGING / self.run_id
            staging.mkdir(parents=True, exist_ok=True)
            part = staging / f"part-{len(self._parts[spec.name]) + 1:04d}.parquet"
            pq.write_table(table, part, compression="zstd")
            fsync_file(part)
            self._parts[spec.name].append((part, table.num_rows))
            self._buffers[spec.name] = []

    def _finish(self) -> None:
        self._cleanup_staging()
        self._closed = True
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    def _cleanup_staging(self) -> None:
        for spec in self.dataset.tables:
            staging = table_dir(self.root, spec.name) / STAGING / self.run_id
            with contextlib.suppress(FileNotFoundError):
                shutil.rmtree(staging)
