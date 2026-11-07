"""One data job at a time per data home."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from pathlib import Path

from tessera.core.errors import TesseraError

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

LOCK_NAME = ".jobs.lock"


class JobLockedError(TesseraError):
    pass


@contextlib.contextmanager
def job_lock(root: Path) -> Iterator[None]:
    """Hold the data home's job lock, or fail at once if another job holds it."""
    path = root / LOCK_NAME
    handle = path.open("a+")
    try:
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise JobLockedError(
                    "Another Tessera data job is running in this data home; try again when it ends."
                ) from None
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        yield
    finally:
        if fcntl is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
