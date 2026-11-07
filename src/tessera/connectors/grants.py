"""Recognize NIH grant numbers in free text.

Publications and registry records cite grants in many shapes: ``R01 CA123456``,
``5R01CA123456-05``, ``R01-CA-123456``, ``CA123456``. NIH's stable identifier across years
is the *core project number*: activity code + institute code + 6-digit serial, e.g.
``R01CA123456``. When the activity code is missing we can still recover the institute +
serial (``CA123456``), which identifies the grant family and can be matched later.
"""

from __future__ import annotations

import re

# Two-letter codes of NIH institutes and centers (and a few retired ones still seen in
# older grants). Restricting to these avoids reading other funders' IDs as NIH grants.
NIH_IC_CODES = frozenset(
    {
        "AA", "AG", "AI", "AR", "AT", "CA", "CL", "DA", "DC", "DE", "DK", "EB", "ES", "EY",
        "GM", "HD", "HG", "HL", "LM", "MD", "MH", "NR", "NS", "OD", "RG", "RR", "TR", "TW",
    }
)  # fmt: skip

_ACTIVITY = r"(?P<act>[A-Z][0-9]{2}|[A-Z]{2}[0-9]|Z[0-9A-Z]{2})"
_WITH_ACTIVITY = re.compile(
    r"(?<![A-Z0-9])[1-9]?\s?"
    + _ACTIVITY
    + r"[\s-]*(?P<ic>[A-Z]{2})[\s-]*(?P<serial>[0-9]{4,6})(?![0-9])"
)
_IC_SERIAL = re.compile(r"(?<![A-Z0-9])(?P<ic>[A-Z]{2})[\s-]*(?P<serial>[0-9]{5,6})(?![0-9])")


def _looks_nih(agency: str | None) -> bool:
    if not agency:
        return False
    upper = agency.upper()
    return "NIH" in upper or "HHS" in upper or "NATIONAL INSTITUTE" in upper


def normalize_grant(
    grant_id: str | None, agency: str | None = None
) -> tuple[str | None, str | None]:
    """Return ``(core_project_num, ic_serial)``; either may be None.

    >>> normalize_grant("5R01 CA-123456-05")
    ('R01CA123456', 'CA123456')
    >>> normalize_grant("CA15704", agency="NCI NIH HHS")
    (None, 'CA015704')
    """
    if not grant_id:
        return None, None
    text = grant_id.upper()
    match = _WITH_ACTIVITY.search(text)
    if match and (match["ic"] in NIH_IC_CODES or match["act"].startswith("Z")):
        serial = match["serial"].zfill(6)
        return f"{match['act']}{match['ic']}{serial}", f"{match['ic']}{serial}"
    match = _IC_SERIAL.search(text)
    if match and match["ic"] in NIH_IC_CODES and (_looks_nih(agency) or agency is None):
        return None, f"{match['ic']}{match['serial'].zfill(6)}"
    return None, None
