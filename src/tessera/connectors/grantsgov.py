"""Funding opportunities from Grants.gov (wave 5). See spec/data-sources.md.

Grants.gov's public search and opportunity APIs need no key. A sync lists every forecast and
posted opportunity (all of them, or only some funding categories and agencies) in one or two
searches, then fetches the details of the opportunities that are new, whose listing changed,
or that were last read more than two weeks ago, and of those held as open that the search no
longer lists (they have closed or been archived, and their record should say so).

Agency contact names, email addresses, and phone numbers are not kept: they are often a named
program officer's, and the opportunity's page links to them.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterator
from datetime import date
from typing import Any

from tessera.connectors.base import Connector, FetchReport, IngestOptions, Payload, Plan, Records
from tessera.connectors.http import SourceError
from tessera.core.clock import utc_now
from tessera.core.errors import TesseraError

SEARCH_ROWS = 1000
REFRESH_DAYS = 14
LISTED = "forecasted|posted"
MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MONTHS = {m: i for i, m in enumerate(MONTH_NAMES, 1)}


def listing_key(hit: dict[str, Any]) -> str:
    """What the search says about an opportunity; when it changes, its details are read again."""
    keys = ("number", "title", "oppStatus", "docType", "openDate", "closeDate")
    return "|".join(str(hit.get(k) or "") for k in keys)


class GrantsGovConnector(Connector):
    name = "grants_gov"
    label = "Grants.gov"
    datasets = ("grants_gov",)
    base_url = "https://api.grants.gov/v1/api/"
    min_interval = 0.5  # no published limit; two requests a second at most
    modes = ("open", "ids")

    def plan(self, options: IngestOptions, checkpoint: str | None, today: date) -> Plan:
        mode = options.mode
        if mode == "auto":
            mode = "ids" if options.ids else "open"
        if mode == "ids":
            ids = sorted({i.strip() for i in options.ids if i.strip().isdigit()}, key=int)
            if not ids:
                raise TesseraError("At least one Grants.gov opportunity ID is required (--ids).")
            return Plan("ids", "", f"{len(ids)} opportunities", params={"ids": ids, "listed": {}})
        if mode != "open":
            raise TesseraError(f"Grants.gov has no mode {mode!r}; choose from open, ids")
        settings = self.config.sources.grants_gov
        agencies, categories = list(settings.agencies), list(settings.categories)
        what = "forecast and posted opportunities"
        if categories:
            what += f" in categories {', '.join(categories)}"
        if agencies:
            what += f" from {', '.join(agencies)}"
        params = {
            "agencies": agencies,
            "categories": categories,
            "known": dict(options.known),
            "max_results": options.max_results,
        }
        return Plan("open", "", what, params=params)

    # -- fetching ------------------------------------------------------------------------

    def fetch(self, plan: Plan, report: FetchReport) -> Iterator[Payload]:
        if plan.mode == "ids":
            ids = plan.params["ids"]
            report.total_found = report.expected = len(ids)
            yield from self._details([(i, None) for i in ids], report)
            return
        hits: list[dict[str, Any]] = []
        start = 0
        while True:
            body: dict[str, Any] = {"oppStatuses": LISTED, "rows": SEARCH_ROWS,
                                    "startRecordNum": start}  # fmt: skip
            if plan.params["categories"]:
                body["fundingCategories"] = "|".join(plan.params["categories"])
            where = {"search": start}
            try:
                response = self.http.post("search2", json=body)
                data = response.json()
            except (SourceError, ValueError) as exc:
                report.fail(where, exc)
                return
            if data.get("errorcode") not in (0, None) or not isinstance(data.get("data"), dict):
                report.fail(where, data.get("msg") or "the search did not answer")
                return
            report.payloads += 1
            yield Payload("search", response.content, "json", utc_now(), request=where)
            page = data["data"].get("oppHits") or []
            hits += page
            start += len(page)
            if not page or start >= (data["data"].get("hitCount") or 0):
                break
        agencies = plan.params["agencies"]
        if agencies:
            hits = [h for h in hits if any(str(h.get("agencyCode", "")).startswith(a)
                                           for a in agencies)]  # fmt: skip
        report.total_found = len(hits)
        known: dict[str, dict[str, Any]] = plan.params["known"]
        wanted: list[tuple[str, dict[str, Any] | None]] = []
        listed = set()
        for hit in hits:
            oid = str(hit.get("id") or "")
            if not oid.isdigit():
                continue
            listed.add(oid)
            held = known.get(oid)
            if held is None or held.get("listing") != listing_key(hit) or held.get("stale"):
                wanted.append((oid, hit))
        # Held as open but no longer listed: read once more, to record that it closed.
        wanted += [(oid, {}) for oid in sorted(known, key=int)
                   if known[oid].get("open") and oid not in listed]  # fmt: skip
        unchanged = len(listed) - sum(1 for _, hit in wanted if hit)
        if unchanged:
            report.notes.append(f"{unchanged:,} listed opportunities are unchanged since last read")
        cap = plan.params.get("max_results")
        if cap is not None and len(wanted) > cap:
            wanted = wanted[:cap]
            report.truncated = True
        report.expected = len(wanted)
        yield from self._details(wanted, report)

    def _details(
        self, wanted: list[tuple[str, dict[str, Any] | None]], report: FetchReport
    ) -> Iterator[Payload]:
        for oid, hit in wanted:
            where: dict[str, Any] = {"opportunity": oid}
            if hit == {}:  # no longer listed by the search
                where["listed_status"] = "unlisted"
            elif hit is not None:
                where["listing"] = listing_key(hit)
                where["listed_status"] = hit.get("oppStatus")
            try:
                response = self.http.post("fetchOpportunity", json={"opportunityId": int(oid)})
                data = response.json()
            except (SourceError, ValueError) as exc:
                report.fail(where, exc)
                continue
            if data.get("errorcode") not in (0, None) or not isinstance(data.get("data"), dict):
                report.fail(where, data.get("msg") or "no such opportunity")
                continue
            report.payloads += 1
            yield Payload("opportunity", response.content, "json", utc_now(), request=where)

    # -- parsing -------------------------------------------------------------------------

    @classmethod
    def parse(cls, payload: Payload) -> Records:
        if payload.kind != "opportunity":
            return {"grants_gov": []}
        record = parse_opportunity(payload.json()["data"], payload.request)
        return {"grants_gov": [record] if record else []}


# --- helpers ------------------------------------------------------------------------------


def gg_date(value: Any) -> date | None:
    """Grants.gov's dates: "May 21, 2026 12:00:00 AM EDT" in details, "05/21/2026" in search."""
    text = str(value or "").strip()
    if m := re.match(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4})", text):
        try:
            return date(int(m[3]), MONTHS[m[1]], int(m[2]))
        except (KeyError, ValueError):
            return None
    if m := re.match(r"(\d{2})/(\d{2})/(\d{4})$", text):
        try:
            return date(int(m[3]), int(m[1]), int(m[2]))
        except ValueError:
            return None
    return None


def _money(value: Any) -> int | None:
    text = re.sub(r"[,$\s]", "", str(value or ""))
    return int(text) if text.isdigit() else None


def plain_text(value: Any) -> str | None:
    """Text from the HTML that descriptions often carry, with paragraphs kept apart."""
    text = str(value or "")
    if not text.strip():
        return None
    text = re.sub(r"(?i)<\s*br\s*/?>|</\s*(p|div|li|h\d)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text)).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return out or None


# NIH notices name their activity codes and clinical-trial rule in the title, e.g.
# "… (R21/R33 Clinical Trial Optional)" or "… (K99/R00 Independent Clinical Trial Not Allowed)".
NIH_TITLE = re.compile(r"\(([^()]*)\)\s*$")
ACTIVITY = re.compile(r"\b([A-Z][A-Z0-9][0-9][A-Z0-9]?|[A-Z]{2}[0-9])\b")
TRIAL = (("not allowed", "not_allowed"), ("required", "required"), ("optional", "optional"))
GUIDE = (("RFA-", "rfa-files"), ("PAR-", "pa-files"), ("PAS-", "pa-files"), ("PA-", "pa-files"),
         ("NOT-", "notice-files"))  # fmt: skip


def nih_details(number: str, title: str) -> dict[str, Any]:
    out: dict[str, Any] = {"activity_codes": [], "clinical_trial": None, "guide_url": None}
    m = NIH_TITLE.search(title or "")
    if m:
        inside = m[1]
        out["activity_codes"] = list(dict.fromkeys(ACTIVITY.findall(inside.split("Clinical")[0])))
        lower = inside.lower()
        if "clinical trial" in lower:
            out["clinical_trial"] = next((v for k, v in TRIAL if k in lower), None)
    for prefix, folder in GUIDE:
        if number.startswith(prefix):
            out["guide_url"] = f"https://grants.nih.gov/grants/guide/{folder}/{number}.html"
            break
    return out


def parse_opportunity(data: dict[str, Any], request: dict[str, Any]) -> dict[str, Any] | None:
    oid = str(data.get("id") or "")
    if not oid.isdigit():
        return None
    forecast = data.get("synopsis") is None and isinstance(data.get("forecast"), dict)
    body: dict[str, Any] = (data.get("forecast") if forecast else data.get("synopsis")) or {}
    number = str(data.get("opportunityNumber") or "")
    title = str(data.get("opportunityTitle") or "")
    agency_code = str(data.get("owningAgencyCode") or body.get("agencyCode") or "")
    agency = (data.get("agencyDetails") or {}).get("agencyName") or body.get("agencyName")
    top = (data.get("topAgencyDetails") or {}).get("agencyName")
    nih = nih_details(number, title) if agency_code.startswith("HHS-NIH") else {}
    closes = body.get("estApplicationResponseDate") if forecast else body.get("responseDate")
    category = (data.get("opportunityCategory") or {}).get("description")
    return {
        "opportunity_id": oid,
        "number": number or None,
        "title": title or None,
        "agency_code": agency_code or None,
        "agency": agency,
        "parent_agency": top if top and top != agency else None,
        "doc_type": "forecast" if forecast else "synopsis",
        "listed_status": request.get("listed_status"),
        "listing": request.get("listing"),
        "category": category,
        "posted": gg_date(body.get("postingDate")),
        "expected_post": gg_date(body.get("estSynopsisPostingDate")) if forecast else None,
        "closes": gg_date(closes),
        "close_note": plain_text(
            body.get("estApplicationResponseDateDesc") if forecast else body.get("responseDateDesc")
        ),
        "archives": gg_date(body.get("archiveDate")),
        "award_ceiling": _money(body.get("awardCeiling")),
        "award_floor": _money(body.get("awardFloor")),
        "estimated_funding": _money(body.get("estimatedFunding")),
        "expected_awards": _money(body.get("numberOfAwards")),
        "cost_sharing": body.get("costSharing")
        if isinstance(body.get("costSharing"), bool)
        else None,
        "applicant_types": [
            str(t.get("id")) for t in body.get("applicantTypes") or [] if t.get("id") is not None
        ],
        "applicant_type_labels": [
            str(t.get("description") or "") for t in body.get("applicantTypes") or []
        ],
        "instruments": [
            str(t.get("description") or t.get("id")) for t in body.get("fundingInstruments") or []
        ],
        "categories": [
            str(t.get("description") or t.get("id"))
            for t in body.get("fundingActivityCategories") or []
        ],
        "assistance_listings": [
            str(c.get("cfdaNumber")) for c in data.get("cfdas") or [] if c.get("cfdaNumber")
        ],
        "description": plain_text(
            body.get("forecastDesc") if forecast else body.get("synopsisDesc")
        ),
        "eligibility_notes": plain_text(body.get("applicantEligibilityDesc")),
        "url": f"https://www.grants.gov/search-results-detail/{oid}",
        "activity_codes": nih.get("activity_codes") or [],
        "clinical_trial": nih.get("clinical_trial"),
        "guide_url": nih.get("guide_url"),
        "last_updated": gg_date(body.get("lastUpdatedDate")),
    }


def opportunity_status(row: dict[str, Any], today: date) -> str:
    """forecasted, posted, closed, or archived, from the record's listing and dates.

    A forecast's dates are estimates, so it stays a forecast while Grants.gov lists it."""
    archives, closes = row.get("archives"), row.get("closes")
    if archives and archives < today:
        return "archived"
    if row.get("listed_status") == "unlisted":
        return "closed"
    if row.get("doc_type") == "forecast":
        return "forecasted"
    if closes and closes < today:
        return "closed"
    return "posted"


__all__ = [
    "GrantsGovConnector",
    "gg_date",
    "listing_key",
    "nih_details",
    "opportunity_status",
    "parse_opportunity",
    "plain_text",
]
