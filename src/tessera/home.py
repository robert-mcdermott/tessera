"""The data home: the one directory that holds an installation's data.

::

    <home>/
      raw/        verbatim API payloads, one folder per ingest run
      lake/       normalized source records (Parquet), one folder per table
      ledger.db   the SQLite ledger (when not using PostgreSQL)
      derived/    rebuildable artifacts: search index, profiles, metrics
      logs/       JSON-lines logs
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from tessera.core.errors import DataHomeError

MARKER = ".tessera-home"


@dataclass(frozen=True)
class DataHome:
    root: Path

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def lake(self) -> Path:
        return self.root / "lake"

    @property
    def derived(self) -> Path:
        return self.root / "derived"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def ledger_file(self) -> Path:
        return self.root / "ledger.db"

    @property
    def marker(self) -> Path:
        return self.root / MARKER

    def subdirs(self) -> tuple[Path, ...]:
        return (self.raw, self.lake, self.derived, self.logs)

    def create(self) -> None:
        """Create the directory layout (idempotent)."""
        try:
            for path in (self.root, *self.subdirs()):
                path.mkdir(parents=True, exist_ok=True)
            if not self.marker.exists():
                self.marker.write_text(
                    "This directory is a Tessera data home. "
                    "Back up ledger.db (or your PostgreSQL ledger); "
                    "everything else can be rebuilt.\n",
                    encoding="utf-8",
                )
        except OSError as exc:
            raise DataHomeError(f"Cannot create data home at {self.root}: {exc}") from exc

    def is_initialized(self) -> bool:
        return self.marker.is_file() and all(p.is_dir() for p in self.subdirs())

    def require(self) -> DataHome:
        """Raise a helpful error unless the data home exists and is writable."""
        if not self.is_initialized():
            raise DataHomeError(
                f"No initialized data home at {self.root}. Run `tessera init` first."
            )
        if not os.access(self.root, os.W_OK):
            raise DataHomeError(f"Data home {self.root} is not writable.")
        return self
