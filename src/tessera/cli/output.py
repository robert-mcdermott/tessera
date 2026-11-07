"""Plain-text output helpers for the CLI."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import Any, TextIO

OK = "ok"
WARN = "warn"
FAIL = "fail"
_MARKS = {OK: "✓", WARN: "!", FAIL: "✗"}


def _supports_unicode(stream: TextIO) -> bool:
    encoding = (getattr(stream, "encoding", None) or "").lower()
    return "utf" in encoding


def mark(state: str, stream: TextIO | None = None) -> str:
    if _supports_unicode(stream or sys.stdout):
        return _MARKS[state]
    return {OK: "OK", WARN: "WARN", FAIL: "FAIL"}[state]


def table(
    rows: Sequence[Sequence[Any]], headers: Sequence[str], stream: TextIO | None = None
) -> None:
    stream = stream or sys.stdout
    cells = [[str(h) for h in headers], *[["" if v is None else str(v) for v in r] for r in rows]]
    widths = [max(len(row[i]) for row in cells) for i in range(len(headers))]
    for index, row in enumerate(cells):
        line = "  ".join(value.ljust(widths[i]) for i, value in enumerate(row)).rstrip()
        print(line, file=stream)
        if index == 0:
            print("  ".join("-" * w for w in widths), file=stream)
