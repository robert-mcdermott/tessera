"""Dates on unit memberships (REC-08).

A membership (a ``member_of`` link from a person to a unit) may carry a start and an end, as
precise as the source allows: a year (``2015``), a month (``2015-07``), or a day
(``2015-07-01``). A membership is current until its end has passed; one without an end is
current. A person may belong to several units at once.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from tessera.core.errors import TesseraError

_ISO = re.compile(r"^(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?$")
_US = re.compile(r"^(\d{1,2})/(?:(\d{1,2})/)?(\d{4})$")  # 7/2015 or 7/1/2015


def parse_partial_date(text: str) -> str | None:
    """``2015``, ``2015-07``, ``2015-07-01``, ``7/2015``, or ``7/1/2015`` as ISO text.

    Returns None for an empty value and raises TesseraError for anything else.
    """
    text = (text or "").strip()
    if not text:
        return None
    if m := _ISO.match(text):
        year, month, day = m.group(1), m.group(2), m.group(3)
    elif m := _US.match(text):
        year, month, day = m.group(3), m.group(1), m.group(2)
    else:
        raise TesseraError(f"{text!r} is not a date (use 2015, 2015-07, or 2015-07-01)")
    try:
        if day:
            return date(int(year), int(month), int(day)).isoformat()
        if month:
            date(int(year), int(month), 1)
            return f"{int(year):04d}-{int(month):02d}"
        return f"{int(year):04d}"
    except ValueError as exc:
        raise TesseraError(f"{text!r} is not a valid date") from exc


def _parts(value: str) -> tuple[int, ...]:
    return tuple(int(p) for p in value.split("-"))


def has_ended(end: str | None, today: date) -> bool:
    """Whether a membership ending at ``end`` (at its precision) is over by ``today``."""
    if not end:
        return False
    parts = _parts(end)
    return parts < (today.year, today.month, today.day)[: len(parts)]


def is_current(qualifiers: dict[str, Any] | None, today: date) -> bool:
    q = qualifiers or {}
    start = q.get("start")
    if start and _parts(start) > (today.year, today.month, today.day)[: len(_parts(start))]:
        return False  # not started yet
    return not has_ended(q.get("end"), today)


__all__ = ["has_ended", "is_current", "parse_partial_date"]
