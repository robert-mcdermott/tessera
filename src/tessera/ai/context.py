"""What a model may see (AI-05): the read projections the API serves, without contact details
and without unreviewed suggestions. Every tool result passes through ``scrub`` before it is sent.
"""

from __future__ import annotations

import re
from typing import Any

# Keys whose values are contact details, wherever they appear.
CONTACT = re.compile(
    r"(^|_)(e?mail|email_address|emails|phone|phones|telephone|fax|address|street|postal|"
    r"contact|contacts|mobile)($|_)",
    re.IGNORECASE,
)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
PHONE = re.compile(r"(?<!\d)(\+?\d[\d\s().-]{7,}\d)(?!\d)")
UNREVIEWED = {"suggested", "deferred", "rejected"}


def scrub(value: Any) -> Any:
    """A copy without contact fields, with email addresses and phone numbers in text blanked,
    and without links that nobody has accepted (a dict with a ``status`` other than
    accepted inside a list is left out)."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if not CONTACT.search(str(k))}
    if isinstance(value, list):
        kept = []
        for item in value:
            if isinstance(item, dict) and str(item.get("status", "accepted")) in UNREVIEWED:
                continue
            kept.append(scrub(item))
        return kept
    if isinstance(value, str):
        return PHONE.sub("[number withheld]", EMAIL.sub("[address withheld]", value))
    return value


__all__ = ["scrub"]
