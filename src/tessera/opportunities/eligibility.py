"""Whether the organization may apply (INT-09), kept apart from whether anyone fits.

Each check says ``meets``, ``does_not_meet``, or ``check`` (a person must read the notice), with
what it was decided from. Some facts are worth knowing but decide nothing (a clinical trial is
required, the dates are a forecast's estimates); they are ``note``. The overall answer is
``does_not_meet`` if any check fails, ``check`` if any needs reading, and ``meets`` otherwise.
Eligibility that depends on the person (career stage, citizenship) is always ``check``.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from tessera.config import Config
from tessera.opportunities.codes import APPLICANT_TYPES

LABELS = {"meets": "Meets", "check": "Check the notice", "does_not_meet": "Does not meet"}
SOON_DAYS = 14
# NIH activity codes whose eligibility depends on the applicant's career stage or training.
CAREER = ("K", "F", "T", "R00", "DP5", "R36", "R25")
OPEN_TO_ALL = ("99",)  # unrestricted
SEE_TEXT = ("25",)  # "others": the eligibility notes say who


def _item(key: str, label: str, result: str, detail: str, source: str) -> dict[str, Any]:
    return {"key": key, "label": label, "result": result, "detail": detail, "from": source}


def _applicants(o: dict[str, Any], ours: tuple[str, ...]) -> dict[str, Any]:
    theirs = list(o["applicant_types"])
    label = "Applicant type"
    if not theirs:
        return _item("applicant", label, "check",
                     "The opportunity lists no applicant types; the notice says who may apply.",
                     "the notice")  # fmt: skip
    if not ours:
        return _item("applicant", label, "check",
                     "Set [organization] applicant_types so that Tessera can compare it with "
                     "the opportunity's applicant types.", "configuration")  # fmt: skip
    shared = [APPLICANT_TYPES.get(c, c) for c in theirs if c in ours]
    if shared:
        return _item("applicant", label, "meets", f"Open to {shared[0].lower()}.",
                     "the opportunity's applicant types")  # fmt: skip
    if any(c in OPEN_TO_ALL for c in theirs):
        return _item("applicant", label, "meets", "Open to any type of applicant.",
                     "the opportunity's applicant types")  # fmt: skip
    if any(c in SEE_TEXT for c in theirs):
        return _item("applicant", label, "check",
                     "Open to others named in the eligibility notes; read them.",
                     "the opportunity's applicant types")  # fmt: skip
    named = ", ".join(APPLICANT_TYPES.get(c, c).lower() for c in theirs[:3])
    return _item("applicant", label, "does_not_meet", f"Only for {named}.",
                 "the opportunity's applicant types")  # fmt: skip


def check(o: dict[str, Any], config: Config, today: date) -> dict[str, Any]:
    """Every check for one opportunity (a catalog view), and the overall answer."""
    items = [_applicants(o, tuple(config.organization.applicant_types))]
    closes = o.get("closes")
    if o["status"] in ("closed", "archived"):
        when = f" on {closes.isoformat()}" if closes else ""
        items.append(_item("deadline", "Deadline", "does_not_meet", f"Closed{when}.", "its dates"))
    elif o["status"] == "forecasted":
        items.append(_item("deadline", "Deadline", "note",
                           "A forecast: its dates and amounts are the sponsor's estimates.",
                           "its dates"))  # fmt: skip
    elif closes is not None:
        days = (closes - today).days
        result = "note" if days <= SOON_DAYS else "meets"
        words = f"Closes in {days} {'day' if days == 1 else 'days'}."
        items.append(_item("deadline", "Deadline", result, words, "its dates"))
    internal = o.get("internal_deadline")
    if internal is not None and o["status"] in ("posted", "forecasted"):
        passed = internal < today
        items.append(_item("internal", "Internal deadline", "does_not_meet" if passed else "check",
                           f"The organization's own deadline {'was' if passed else 'is'} "
                           f"{internal.isoformat()}.", "this organization"))  # fmt: skip
    if o.get("limited_submission"):
        items.append(_item("limited", "Limited submission", "check",
                           "The organization may send only a few applications, chosen by an "
                           "internal competition first.", "this organization"))  # fmt: skip
    careers = [a for a in o.get("activity_codes") or [] if a.startswith(CAREER) or a in CAREER]
    if careers:
        items.append(_item("career", "Career stage", "check",
                           f"{', '.join(careers)} awards depend on the applicant's career stage "
                           "or training; read the notice.", "the activity code"))  # fmt: skip
    if o.get("cost_sharing"):
        words = "The sponsor requires cost sharing or matching."
        items.append(_item("cost_sharing", "Cost sharing", "check", words, "the opportunity"))
    trial = o.get("clinical_trial")
    if trial:
        words = {"required": "A clinical trial is required.",
                 "optional": "Clinical trials are optional.",
                 "not_allowed": "Clinical trials are not allowed."}[trial]  # fmt: skip
        items.append(_item("clinical_trial", "Clinical trial", "note", words, "the title"))
    results = {i["result"] for i in items}
    overall = ("does_not_meet" if "does_not_meet" in results
               else "check" if "check" in results else "meets")  # fmt: skip
    return {"overall": overall, "label": LABELS[overall], "checks": items}


__all__ = ["LABELS", "check"]
