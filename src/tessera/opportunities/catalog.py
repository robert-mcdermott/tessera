"""Every funding opportunity in one shape (INT-09, INT-12).

Federal opportunities come from Grants.gov and live in the lake; the opportunities an
organization tracks itself (foundation calls, internal pilot funds, limited submissions) live in
the ledger. Both are read here into the same record, addressed ``opp:<id>``: Grants.gov's
numeric ID, or the ledger's ``opl_`` ID.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from tessera.connectors.grantsgov import opportunity_status
from tessera.lake import Lake
from tessera.ledger.tables import opportunity

FEDERAL, LOCAL = "grants_gov", "local"
STATUS_LABELS = {"posted": "Open", "forecasted": "Forecast", "closed": "Closed",
                 "archived": "Archived"}  # fmt: skip
OPEN = ("posted", "forecasted")

# Grants.gov's codes for who may apply (its "eligible applicants"), in short.
APPLICANT_TYPES = {
    "00": "State governments",
    "01": "County governments",
    "02": "City or township governments",
    "04": "Special district governments",
    "05": "Independent school districts",
    "06": "Public and state-controlled institutions of higher education",
    "07": "Federally recognized Native American tribal governments",
    "08": "Public and Indian housing authorities",
    "11": "Native American tribal organizations",
    "12": "Nonprofits with 501(c)(3) status, other than institutions of higher education",
    "13": "Nonprofits without 501(c)(3) status, other than institutions of higher education",
    "20": "Private institutions of higher education",
    "21": "Individuals",
    "22": "For-profit organizations other than small businesses",
    "23": "Small businesses",
    "25": "Others (see the eligibility notes)",
    "99": "Unrestricted (see the eligibility notes)",
}
KIND_LABELS = {"federal": "Federal", "foundation": "Foundation", "internal": "Internal",
               "state": "State", "industry": "Industry", "other": "Other"}  # fmt: skip


def ref_of(oid: str) -> str:
    return f"opp:{oid}"


def id_of(ref: str) -> str:
    return ref.removeprefix("opp:")


def is_local(oid: str) -> bool:
    return id_of(oid).startswith("opl_")


FEDERAL_SQL = "SELECT * FROM grants_gov_opportunity"


def _federal(lake: Lake, ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
    if ids is None:
        return lake.rows(FEDERAL_SQL)
    wanted = sorted({i for i in ids if i.isdigit()})
    if not wanted:
        return []
    marks = ", ".join("?" for _ in wanted)
    return lake.rows(f"{FEDERAL_SQL} WHERE opportunity_id IN ({marks})", wanted)


def _local(engine: Engine, ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
    query = select(opportunity)
    if ids is not None:
        wanted = sorted({i for i in ids if i.startswith("opl_")})
        if not wanted:
            return []
        query = query.where(opportunity.c.id.in_(wanted))
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(query).mappings()]


def federal_view(r: dict[str, Any], today: date) -> dict[str, Any]:
    status = opportunity_status(r, today)
    return {
        "id": ref_of(r["opportunity_id"]),
        "source": FEDERAL,
        "source_label": "Grants.gov",
        "kind": "federal",
        "number": r["number"],
        "title": r["title"] or r["number"] or r["opportunity_id"],
        "sponsor": r["agency"],
        "parent_sponsor": r["parent_agency"],
        "agency_code": r["agency_code"],
        "status": status,
        "status_label": STATUS_LABELS[status],
        "forecast": r["doc_type"] == "forecast",
        "posted": r["posted"],
        "expected_post": r["expected_post"],
        "closes": r["closes"],
        "close_note": r["close_note"],
        "internal_deadline": None,
        "archives": r["archives"],
        "award_floor": r["award_floor"],
        "award_ceiling": r["award_ceiling"],
        "estimated_funding": r["estimated_funding"],
        "expected_awards": r["expected_awards"],
        "cost_sharing": r["cost_sharing"],
        "applicant_types": list(r["applicant_types"] or []),
        "applicant_type_labels": [APPLICANT_TYPES.get(c, c) for c in r["applicant_types"] or []],
        "instruments": list(r["instruments"] or []),
        "categories": list(r["categories"] or []),
        "assistance_listings": list(r["assistance_listings"] or []),
        "activity_codes": list(r["activity_codes"] or []),
        "clinical_trial": r["clinical_trial"],
        "limited_submission": None,
        "description": r["description"],
        "eligibility_notes": r["eligibility_notes"],
        "url": r["url"],
        "guide_url": r["guide_url"],
        "last_updated": r["last_updated"],
        "retrieved_at": r.get("_retrieved_at"),
    }


def local_status(r: dict[str, Any], today: date) -> str:
    if r["archived_at"] is not None:
        return "archived"
    if r["closes_on"] and r["closes_on"] < today:
        return "closed"
    if r["posted_on"] and r["posted_on"] > today:
        return "forecasted"
    return "posted"


def local_view(r: dict[str, Any], today: date) -> dict[str, Any]:
    status = local_status(r, today)
    return {
        "id": ref_of(r["id"]),
        "source": LOCAL,
        "source_label": r["source"],
        "kind": r["kind"],
        "number": r["number"],
        "title": r["title"],
        "sponsor": r["sponsor"] or r["source"],
        "parent_sponsor": None,
        "agency_code": None,
        "status": status,
        "status_label": STATUS_LABELS[status],
        "forecast": status == "forecasted",
        "posted": r["posted_on"],
        "expected_post": r["posted_on"] if status == "forecasted" else None,
        "closes": r["closes_on"],
        "close_note": None,
        "internal_deadline": r["internal_deadline"],
        "archives": None,
        "award_floor": r["award_floor"],
        "award_ceiling": r["award_ceiling"],
        "estimated_funding": None,
        "expected_awards": None,
        "cost_sharing": None,
        "applicant_types": list(r["applicant_types"] or []),
        "applicant_type_labels": [APPLICANT_TYPES.get(c, c) for c in r["applicant_types"] or []],
        "instruments": [],
        "categories": [],
        "assistance_listings": [],
        "activity_codes": [],
        "clinical_trial": None,
        "limited_submission": bool(r["limited_submission"]),
        "description": r["description"],
        "eligibility_notes": r["eligibility_notes"],
        "url": r["url"],
        "guide_url": None,
        "last_updated": r["updated_at"].date() if r["updated_at"] else None,
        "external_id": r["external_id"],
        "created_by": r["created_by"],
        "updated_by": r["updated_by"],
        "updated_at": r["updated_at"],
    }


def get(engine: Engine, lake: Lake, ref: str, today: date | None = None) -> dict[str, Any] | None:
    """One opportunity by its address (``opp:<id>``, or the bare ID)."""
    today = today or date.today()
    oid = id_of(ref)
    if is_local(oid):
        rows = _local(engine, [oid])
        return local_view(rows[0], today) if rows else None
    rows = _federal(lake, [oid])
    return federal_view(rows[0], today) if rows else None


def everything(engine: Engine, lake: Lake, today: date | None = None) -> list[dict[str, Any]]:
    """Every opportunity, federal and the organization's own, in one shape."""
    today = today or date.today()
    out = [federal_view(r, today) for r in _federal(lake)]
    out += [local_view(r, today) for r in _local(engine)]
    return out


def some(
    engine: Engine, lake: Lake, refs: Iterable[str], today: date | None = None
) -> list[dict[str, Any]]:
    today = today or date.today()
    ids = [id_of(r) for r in refs]
    out = [federal_view(r, today) for r in _federal(lake, ids)]
    out += [local_view(r, today) for r in _local(engine, ids)]
    return out


__all__ = [
    "APPLICANT_TYPES",
    "FEDERAL",
    "KIND_LABELS",
    "LOCAL",
    "OPEN",
    "STATUS_LABELS",
    "everything",
    "get",
    "id_of",
    "is_local",
    "ref_of",
    "some",
]
