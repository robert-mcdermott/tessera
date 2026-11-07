"""Time helpers. Tessera stores and compares all instants in UTC."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """The current instant as a timezone-aware UTC datetime (microsecond precision)."""
    return datetime.now(UTC)


def to_utc(value: datetime) -> datetime:
    """Convert an aware datetime to UTC. Naive datetimes are assumed to already be UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def isoformat(value: datetime) -> str:
    """ISO 8601 with a ``Z`` suffix, e.g. ``2026-11-07T18:04:05.123456Z``."""
    return to_utc(value).isoformat().replace("+00:00", "Z")
