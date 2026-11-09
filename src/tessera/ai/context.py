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
# Telephone numbers as people write them: +44 20 7946 0958, (206) 555-0100, 206.555.0100. Dates,
# ORCID iDs, PubMed IDs, and grant numbers are digits too, and must pass.
PHONE = re.compile(
    r"(?<![\w-])(?:\+\d{1,3}[\s.-]?(?:\(?\d{1,4}\)?[\s.-]?){2,5}\d{2,4}"
    r"|(?:1[\s.-])?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4})(?![\w-])"
)
UNREVIEWED = {"suggested", "deferred", "rejected"}
# Counts and lists of what nobody has reviewed yet.
SUGGESTION_KEYS = {"suggested", "suggested_by_band", "suggestions", "suggested_count"}


def scrub(value: Any) -> Any:
    """A copy without contact fields, with email addresses and phone numbers in text blanked,
    and without links that nobody has accepted (a dict in a list whose ``status``, or whose
    ``assertion``'s status, is suggested, deferred, or rejected is left out, as are counts of
    suggestions)."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()
                if not CONTACT.search(str(k)) and k not in SUGGESTION_KEYS}  # fmt: skip
    if isinstance(value, list):
        return [scrub(item) for item in value if not _unreviewed(item)]
    if isinstance(value, str):
        return PHONE.sub("[number withheld]", EMAIL.sub("[address withheld]", value))
    return value


def _unreviewed(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    assertion = item.get("assertion")
    status = assertion.get("status") if isinstance(assertion, dict) else item.get("status")
    return str(status) in UNREVIEWED


__all__ = ["scrub"]
