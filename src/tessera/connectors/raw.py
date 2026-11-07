"""The raw zone: response bodies as fetched, kept so the lake can be rebuilt offline.

Layout: ``raw/<source>/<run_id>/<seq>-<kind>.<json|xml>.gz`` plus ``index.jsonl`` with one
line per payload (kind, media type, retrieval time, request summary, size).
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from tessera.connectors.base import Payload
from tessera.core.clock import isoformat
from tessera.core.errors import TesseraError

INDEX = "index.jsonl"


class RawStore:
    def __init__(self, raw_root: Path, source: str, run_id: str) -> None:
        self.directory = raw_root / source / run_id
        self._seq = 0

    def save(self, payload: Payload) -> str:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._seq += 1
        name = f"{self._seq:05d}-{payload.kind}.{payload.media_type}.gz"
        (self.directory / name).write_bytes(gzip.compress(payload.content, mtime=0))
        entry = {
            "seq": self._seq,
            "file": name,
            "kind": payload.kind,
            "media_type": payload.media_type,
            "retrieved_at": isoformat(payload.retrieved_at),
            "bytes": len(payload.content),
            "request": payload.request,
            "source_version": payload.source_version,
        }
        with (self.directory / INDEX).open("a", encoding="utf-8") as index:
            index.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
        return name


def iter_payloads(raw_root: Path, source: str, run_id: str) -> Iterator[Payload]:
    """Read back the payloads of one run, in the order they were fetched."""
    directory = raw_root / source / run_id
    index = directory / INDEX
    if not index.is_file():
        raise TesseraError(f"No raw payloads for {source} run {run_id} in {directory}")
    with index.open(encoding="utf-8") as lines:
        for line in lines:
            if not line.strip():
                continue
            entry = json.loads(line)
            yield Payload(
                kind=entry["kind"],
                content=gzip.decompress((directory / entry["file"]).read_bytes()),
                media_type=entry["media_type"],
                retrieved_at=datetime.fromisoformat(entry["retrieved_at"].replace("Z", "+00:00")),
                request=entry.get("request") or {},
                source_version=entry.get("source_version"),
            )


def runs_with_raw(raw_root: Path, source: str) -> list[str]:
    directory = raw_root / source
    if not directory.is_dir():
        return []
    return sorted(p.name for p in directory.iterdir() if (p / INDEX).is_file())
