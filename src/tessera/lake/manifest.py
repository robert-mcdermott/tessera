"""Per-table manifests: the list of live Parquet files, swapped atomically.

Readers only open files listed in a table's manifest, so a writer can add files at
leisure and publish them all at once by replacing ``_manifest.json``.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tessera.core.clock import isoformat, utc_now
from tessera.core.errors import LakeError
from tessera.core.fsutil import atomic_write_json

MANIFEST_NAME = "_manifest.json"
LOCK_NAME = ".lock"

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]


@dataclass
class FileEntry:
    path: str  # relative to the table directory, e.g. "data/run_…-0001.parquet"
    run_id: str
    rows: int
    added_at: str

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "run_id": self.run_id,
            "rows": self.rows,
            "added_at": self.added_at,
        }


@dataclass
class Manifest:
    table: str
    schema_version: int
    generation: int = 0
    files: list[FileEntry] = field(default_factory=list)
    superseded: list[dict[str, str]] = field(default_factory=list)
    updated_at: str | None = None

    @classmethod
    def empty(cls, table: str, schema_version: int) -> Manifest:
        return cls(table=table, schema_version=schema_version)

    def to_json(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "schema_version": self.schema_version,
            "generation": self.generation,
            "files": [entry.to_json() for entry in self.files],
            "superseded": self.superseded,
            "updated_at": self.updated_at,
        }

    @property
    def rows(self) -> int:
        return sum(entry.rows for entry in self.files)


def table_dir(lake_root: Path, table: str) -> Path:
    return lake_root / table


def read_manifest(lake_root: Path, table: str, schema_version: int) -> Manifest:
    path = table_dir(lake_root, table) / MANIFEST_NAME
    try:
        raw = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return Manifest.empty(table, schema_version)
    except (OSError, json.JSONDecodeError) as exc:
        raise LakeError(f"Unreadable manifest for lake table {table}: {exc}") from exc
    return Manifest(
        table=raw["table"],
        schema_version=int(raw.get("schema_version", schema_version)),
        generation=int(raw.get("generation", 0)),
        files=[FileEntry(**entry) for entry in raw.get("files", [])],
        superseded=list(raw.get("superseded", [])),
        updated_at=raw.get("updated_at"),
    )


def write_manifest(lake_root: Path, manifest: Manifest) -> None:
    manifest.generation += 1
    manifest.updated_at = isoformat(utc_now())
    atomic_write_json(table_dir(lake_root, manifest.table) / MANIFEST_NAME, manifest.to_json())


@contextlib.contextmanager
def table_lock(lake_root: Path, table: str, timeout: float = 60.0) -> Iterator[None]:
    """Exclusive lock for changing a table's manifest (across processes)."""
    directory = table_dir(lake_root, table)
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / LOCK_NAME
    if fcntl is not None:
        with lock_path.open("a+") as handle:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        raise LakeError(
                            f"Timed out waiting for lock on lake table {table}"
                        ) from None
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    # Fallback: exclusive-create lock file.  # pragma: no cover
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                raise LakeError(f"Timed out waiting for lock on lake table {table}") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            lock_path.unlink()
