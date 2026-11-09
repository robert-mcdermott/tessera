"""Build an expensive result once, however many ask for it at the same moment.

When several requests need the same thing that is not ready yet (an Insight figure after the
nightly sync, say), the first builds it and the others wait for that result, instead of each
building its own at the same time and slowing every request in the process. A failure reaches
everyone who was waiting for it and is not kept: the next request tries again.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Hashable
from concurrent.futures import Future
from typing import TypeVar

T = TypeVar("T")


class Once:
    """Requests for one key at the same moment share one build."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._building: dict[Hashable, Future] = {}

    def run(self, key: Hashable, build: Callable[[], T]) -> T:
        with self._lock:
            future = self._building.get(key)
            owner = future is None
            if future is None:
                future = Future()
                self._building[key] = future
        if not owner:
            return future.result()
        try:
            value = build()
        except BaseException as exc:
            future.set_exception(exc)
            raise
        else:
            future.set_result(value)
            return value
        finally:
            with self._lock:
                self._building.pop(key, None)

    def building(self) -> int:
        """How many builds are under way (for tests)."""
        with self._lock:
            return len(self._building)


__all__ = ["Once"]
