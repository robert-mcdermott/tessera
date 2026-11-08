"""Typed, time-ordered identifiers.

Internal IDs look like ``per_0J8ZQ3W5T4K2M9B7XHRC1DGFNE``: a short type prefix, an
underscore, and a 26-character Crockford base32 encoding of a UUIDv7 (RFC 9562).
They sort by creation time, never contain ambiguous characters, and say what they
refer to at a glance.
"""

from __future__ import annotations

import os
import re
import threading
import time
import uuid

# Prefixes for entity types that Tessera owns. External records (publications,
# grants, studies) use their natural keys instead, e.g. ``pub:12345678``.
PREFIXES = {
    "per": "person",
    "unt": "unit",
    "asr": "assertion",
    "run": "run",
    "lab": "lab",
    "usr": "user",
    "mrg": "merge",
    "bat": "batch",
    "opn": "opening",
    "thm": "theme",
    "coh": "cohort",
    "srh": "saved search",
    "rpt": "report",
    "snp": "snapshot",
    "opl": "opportunity",
    "tem": "team",
}

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE = {c: i for i, c in enumerate(_CROCKFORD)}
_ID_RE = re.compile(r"^([a-z]{3})_([0-9A-HJKMNP-TV-Z]{26})$")

_lock = threading.Lock()
_last_ms = -1
_last_seq = 0


def uuid7() -> uuid.UUID:
    """Return a UUIDv7.

    Within one process, IDs are strictly increasing: if two are created in the same
    millisecond, the 12-bit ``rand_a`` field is used as a counter.
    """
    global _last_ms, _last_seq
    with _lock:
        ms = time.time_ns() // 1_000_000
        if ms <= _last_ms:
            ms = _last_ms
            _last_seq += 1
            if _last_seq > 0xFFF:  # counter exhausted: borrow the next millisecond
                ms += 1
                _last_seq = 0
        else:
            _last_seq = int.from_bytes(os.urandom(2), "big") & 0x3FF  # leave headroom
        _last_ms = ms
        seq = _last_seq
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= seq << 64
    value |= 0b10 << 62
    value |= rand_b
    return uuid.UUID(int=value)


def _encode(value: int) -> str:
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def new_id(prefix: str) -> str:
    """Create a new typed ID such as ``per_…``."""
    if prefix not in PREFIXES:
        raise ValueError(f"unknown ID prefix {prefix!r}; expected one of {sorted(PREFIXES)}")
    return f"{prefix}_{_encode(uuid7().int)}"


def parse_id(value: str) -> tuple[str, uuid.UUID]:
    """Split a typed ID into its prefix and UUID. Raises ``ValueError`` if malformed."""
    match = _ID_RE.match(value)
    if not match or match.group(1) not in PREFIXES:
        raise ValueError(f"not a Tessera ID: {value!r}")
    number = 0
    for ch in match.group(2):
        number = (number << 5) | _DECODE[ch]
    if number >> 128:
        raise ValueError(f"not a Tessera ID: {value!r}")
    return match.group(1), uuid.UUID(int=number)


def is_id(value: str, prefix: str | None = None) -> bool:
    """True if ``value`` is a well-formed typed ID (optionally with the given prefix)."""
    try:
        found, _ = parse_id(value)
    except ValueError:
        return False
    return prefix is None or found == prefix
