"""The assistant's tools: read-only questions to Tessera's own API, asked as the person asking.

A tool never reads the ledger or the lake itself. It asks the API's GET routes, in process, with
the asker's own session, so every answer passes the same access policy as the page that shows it
(AI-08), and nothing a tool does can change anything: the fetcher only ever sends GET.

What comes back is cut down to what a question needs, passed through ``scrub`` (no contact
details, no unreviewed suggestions), and every record in it is noted: those, and only those, are
what an answer may cite.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote, urlencode

from tessera.ai.context import scrub
from tessera.insight.metrics import all_metrics

# (status, body) for a GET of an API path with query parameters.
Fetch = Callable[[str, Mapping[str, Any]], tuple[int, Any]]

RESULT_CHARS = 12_000  # the most one tool result may send the model
TEXT_CHARS = 1_200  # abstracts, descriptions, and page text are cut to this

REF = re.compile(
    r"^(?:per_[0-9A-HJKMNP-TV-Z]{26}"
    r"|pub:\d{1,12}"
    r"|grt:[A-Z0-9]{4,20}"
    r"|stu:NCT\d{8}"
    r"|opp:[A-Za-z0-9_.-]{1,64}"
    r"|grp:[a-z0-9][a-z0-9-]{0,99}"
    r"|fig:\d{1,3})$"
)
KINDS = {
    "per_": "person",
    "pub:": "publication",
    "grt:": "grant",
    "stu:": "study",
    "opp:": "opportunity",
    "grp:": "group",
    "fig:": "figure",
}
INSIGHT_PAGES = {
    "Publications": "/insight",
    "Funding": "/insight/funding",
    "Topics": "/insight/topics",
    "Studies": "/insight/studies",
    "Impact": "/insight/impact",
}


class ToolError(Exception):
    """A tool could not answer; the model is told why."""


def kind_of(ref: str) -> str:
    return KINDS.get(ref[:4], "record")


def normalize_ref(value: Any) -> str:
    """A reference as the model wrote it, tidied: ``PMID 123`` and ``pmid:123`` are ``pub:123``,
    a bare NCT number is a study, ``group:slug`` is ``grp:slug``."""
    text = str(value or "").strip().strip("[]()<>`'\".,;")
    lowered = text.lower()
    if re.fullmatch(r"pmid[:\s]*\d+", lowered):
        return "pub:" + re.sub(r"\D", "", text)
    if re.fullmatch(r"nct\d{8}", lowered):
        return "stu:" + text.upper()
    if lowered.startswith("group:"):
        return "grp:" + text[6:]
    if lowered.startswith(("stu:nct", "grt:")):
        return text[:4].lower() + text[4:].upper()
    return text


def href_of(ref: str) -> str:
    kind = kind_of(ref)
    natural = quote(ref[4:] if ":" in ref[:4] else ref, safe="")
    return {
        "person": f"/people/{natural}",
        "publication": f"/publications/{natural}",
        "grant": f"/grants/{natural}",
        "study": f"/studies/{natural}",
        "opportunity": f"/opportunities/{natural}",
        "group": f"/groups/{natural}",
    }.get(kind, "")


@dataclass
class Record:
    """A record a tool returned in this exchange: what an answer may cite."""

    ref: str
    kind: str
    title: str
    subtitle: str | None
    href: str


def _cut(text: Any, limit: int = TEXT_CHARS) -> str | None:
    if text is None:
        return None
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _linked(item: dict[str, Any]) -> list[dict[str, Any]]:
    """The Tessera people an author or official is confirmed to be."""
    out = []
    for link in item.get("people") or []:
        person, assertion = link.get("person") or {}, link.get("assertion") or {}
        if person.get("id") and assertion.get("status", "accepted") == "accepted":
            out.append({"ref": person["id"], "name": person.get("name")})
    return out


def _names(items: Any, limit: int = 8) -> list[dict[str, Any]]:
    """People in a list (investigators, officials, members), as ref and name."""
    out = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        pid = item.get("person_id") or (
            item.get("id") if str(item.get("id", "")).startswith("per_") else None
        )
        name = item.get("name") or item.get("display_name")
        if pid and name:
            out.append({"ref": pid, "name": name})
        if len(out) >= limit:
            break
    return out


TOOLS: list[dict[str, Any]] = [
    {
        "name": "search",
        "description": "Search Tessera's records by words: people, publications, grants, studies "
        "(clinical trials), funding opportunities, and groups (labs, programs, divisions). Leave "
        "kind out to search every kind at once. Returns matching records with their refs.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A few words, such as a topic or a name",
                },
                "kind": {
                    "type": "string",
                    "enum": ["person", "publication", "grant", "study", "opportunity", "group"],
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "open_record",
        "description": "Open one record by its ref: a person (per_…), publication (pub:…), grant "
        "(grt:…), study (stu:…), opportunity (opp:…), or group (grp:…). Returns its details.",
        "parameters": {
            "type": "object",
            "properties": {"ref": {"type": "string"}},
            "required": ["ref"],
        },
    },
    {
        "name": "experts",
        "description": "Who works on a topic: people ranked by how much of their confirmed work "
        "is about it, with the subjects that matched.",
        "parameters": {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"],
        },
    },
    {
        "name": "work_of",
        "description": "A person's or a group's work: confirmed publications, grants, and studies "
        "(for a group, also its people and topics). Give a person's ref (per_…) or a group's "
        "(grp:…).",
        "parameters": {
            "type": "object",
            "properties": {"ref": {"type": "string"}},
            "required": ["ref"],
        },
    },
    {
        "name": "insight_figure",
        "description": "A portfolio figure from Insight, counting confirmed records only: a "
        "metric over a cohort and a range of years. Cohorts: organization (the default), "
        "group:<group slug>, group:<group slug>+below (with its sub-groups), or "
        "people:<per_…>,<per_…>.",
        "parameters": {
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": []},  # filled in below
                "cohort": {"type": "string"},
                "start": {"type": "integer", "description": "First year"},
                "end": {"type": "integer", "description": "Last year"},
            },
            "required": ["metric"],
        },
    },
    {
        "name": "impact_thread",
        "description": "What followed a grant (grt:…), a publication (pub:…), or a person's work "
        "(per_…): papers, studies, and clinical articles citing the papers, with how each is "
        "linked. Association, not causation.",
        "parameters": {
            "type": "object",
            "properties": {"ref": {"type": "string"}},
            "required": ["ref"],
        },
    },
    {
        "name": "opportunity_fit",
        "description": "Who fits a funding opportunity (opp:…) and why, and whether the "
        "organization may apply.",
        "parameters": {
            "type": "object",
            "properties": {"ref": {"type": "string"}},
            "required": ["ref"],
        },
    },
    {
        "name": "opportunities_for",
        "description": "Open funding opportunities that fit a person (per_…) or a group (grp:…).",
        "parameters": {
            "type": "object",
            "properties": {"ref": {"type": "string"}},
            "required": ["ref"],
        },
    },
]
_METRICS = {m.id: m for m in all_metrics()}
TOOLS[4]["parameters"]["properties"]["metric"]["enum"] = sorted(_METRICS)
TOOLS[4]["description"] += (
    " Metrics: "
    + "; ".join(f"{m.id} ({m.name})" for m in sorted(_METRICS.values(), key=lambda m: m.id))
    + "."
)
TOOL_NAMES = frozenset(t["name"] for t in TOOLS)


class Toolbox:
    """Runs the model's tool calls for one exchange, and remembers what they returned."""

    def __init__(self, fetch: Fetch, inherited: list[Record] | None = None) -> None:
        self.fetch = fetch
        self.records: dict[str, Record] = {r.ref: r for r in inherited or []}
        self.calls: list[dict[str, Any]] = []
        self.figures = sum(r.kind == "figure" for r in self.records.values())
        self._returned: list[str] = []

    # --- the model's side ------------------------------------------------------------------

    def run(self, name: str, arguments: Mapping[str, Any]) -> str:
        """One call, as JSON text for the model. Failures are told to the model, not raised."""
        self._returned: list[str] = []
        handler = getattr(self, f"_tool_{name}", None) if name in TOOL_NAMES else None
        try:
            if handler is None:
                raise ToolError(f"There is no tool named {name!r}.")
            result = scrub(
                handler(**{k: v for k, v in dict(arguments).items() if v not in (None, "")})
            )
            status = "ok"
        except ToolError as exc:
            result, status = {"error": str(exc)}, "error"
        except TypeError as exc:  # arguments the tool does not take
            result, status = {"error": f"Those arguments do not fit {name}: {exc}"}, "error"
        text = json.dumps(result, ensure_ascii=False, default=str)
        if len(text) > RESULT_CHARS:
            text = text[:RESULT_CHARS] + " …(cut)"
        self.calls.append(
            {
                "name": name,
                "arguments": dict(arguments),
                "status": status,
                "refs": list(dict.fromkeys(self._returned)),
                "error": result.get("error") if status == "error" else None,
            }
        )
        return text

    def searched(self) -> list[str]:
        """What the tools looked for, in words, for a decline."""
        out = []
        for call in self.calls:
            args = call["arguments"]
            what = args.get("query") or args.get("topic") or args.get("ref") or args.get("metric")
            found = len(call["refs"])
            out.append(
                f"{call['name'].replace('_', ' ')} “{what}”: "
                f"{found} record{'s' if found != 1 else ''}"
            )
        return out

    def log(self) -> list[dict[str, Any]]:
        return [{**c, "records": [asdict(self.records[r]) for r in c["refs"]]} for c in self.calls]

    # --- plumbing ----------------------------------------------------------------------------

    def _get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        status, body = self.fetch(path, {k: v for k, v in (params or {}).items() if v is not None})
        if status == 404:
            raise ToolError("Nothing has that reference.")
        if status in (401, 403):
            raise ToolError("That is not available to the person asking.")
        if status >= 400 or not isinstance(body, dict):
            detail = body.get("detail") if isinstance(body, dict) else None
            raise ToolError(_cut(detail, 300) or f"Tessera answered {status}.")
        return body.get("data")

    def _ref(self, value: Any, *kinds: str) -> str:
        ref = normalize_ref(value)
        if not REF.match(ref) or (kinds and kind_of(ref) not in kinds):
            wanted = " or ".join(kinds) if kinds else "a record"
            raise ToolError(
                f"{value!r} is not a ref for {wanted}; refs look like per_…, pub:…, "
                "grt:…, stu:NCT…, opp:…, or grp:…, as other tools return them."
            )
        return ref

    def _note(
        self, ref: Any, kind: str, title: Any, subtitle: Any = None, href: str | None = None
    ) -> str | None:
        ref = normalize_ref(ref)
        if not ref or not REF.match(ref) or not title:
            return None
        if ref not in self.records:
            self.records[ref] = Record(
                ref, kind, _cut(title, 300) or ref, _cut(subtitle, 200), href or href_of(ref)
            )
        self._returned.append(ref)
        return ref

    def _people(self, items: Any, limit: int = 8) -> list[dict[str, Any]]:
        people = _names(items, limit)
        for p in people:
            self._note(p["ref"], "person", p["name"])
        return people

    # --- compact forms -----------------------------------------------------------------------

    def _search_item(self, item: dict[str, Any]) -> dict[str, Any]:
        kind, s = item.get("kind"), item.get("summary") or {}
        ref = self._note(item.get("ref"), kind, item.get("title"), item.get("subtitle"))
        out: dict[str, Any] = {
            "ref": ref,
            "title": item.get("title"),
            "about": item.get("subtitle"),
        }
        if item.get("year"):
            out["year"] = item["year"]
        if kind == "person":
            out["units"] = s.get("units")
            out["confirmed_papers"] = (s.get("counts") or {}).get("accepted")
        elif kind == "publication":
            out["authors"] = _cut(s.get("authors"), 200)
            out["tessera_people"] = self._people(s.get("members"), 6)
        elif kind == "grant":
            out.update(
                activity=s.get("activity_code"),
                institute=s.get("institute"),
                years=s.get("fiscal_years"),
                active=s.get("is_active"),
                investigators=self._people(s.get("investigators"), 6),
            )
        elif kind == "study":
            out.update(
                status=s.get("registry_status"),
                phases=s.get("phases"),
                conditions=s.get("conditions"),
                officials=self._people(s.get("officials"), 4),
            )
        elif kind == "opportunity":
            out.update(
                number=s.get("number"),
                sponsor=s.get("sponsor"),
                status=s.get("status_label"),
                closes=s.get("closes"),
            )
        return out

    def _group_item(self, g: dict[str, Any]) -> dict[str, Any]:
        ref = self._note(f"grp:{g.get('slug')}", "group", g.get("name"), g.get("kind_label"))
        return {
            "ref": ref,
            "name": g.get("name"),
            "kind": g.get("kind_label"),
            "description": _cut(g.get("description"), 300),
            "members": g.get("members"),
            "leaders": [x for x in g.get("leaders") or [] if isinstance(x, str)] or None,
            "parents": [p.get("name") for p in g.get("parents") or []],
        }

    def _person(self, p: dict[str, Any]) -> dict[str, Any]:
        ref = self._note(
            p.get("id"),
            "person",
            p.get("name"),
            ", ".join(u["unit"] for u in p.get("units") or [] if u.get("current")),
        )
        orcid = next(
            (i.get("value") for i in p.get("identifiers") or [] if i.get("scheme") == "orcid"), None
        )
        counts = p.get("counts") or {}
        return {
            "ref": ref,
            "name": p.get("name"),
            "status": p.get("status"),
            "orcid": orcid,
            "positions": [
                {
                    "group": u.get("unit"),
                    "group_ref": f"grp:{u['group_slug']}" if u.get("group_slug") else None,
                    "role": u.get("role_label"),
                    "title": u.get("title"),
                    "since": u.get("start"),
                    "until": u.get("end"),
                    "current": u.get("current"),
                }
                for u in p.get("units") or []
            ],
            "topics": (p.get("concepts") or [])[:12],
            "confirmed_papers": (counts.get("publications") or {}).get("accepted"),
            "grants": counts.get("grants"),
            "studies": counts.get("studies"),
        }

    def _publication(self, d: dict[str, Any]) -> dict[str, Any]:
        ref = self._note(
            f"pub:{d.get('pmid')}", "publication", d.get("title"), d.get("journal_abbrev")
        )
        authors = [
            a.get("collective_name") or f"{a.get('given') or ''} {a.get('family') or ''}".strip()
            for a in d.get("authors") or []
        ]
        tessera = [p for a in d.get("authors") or [] for p in _linked(a)]
        for p in tessera:
            self._note(p["ref"], "person", p["name"])
        grants = []
        for g in d.get("grants") or []:
            core = (g.get("summary") or {}).get("core_project_num") or str(
                (g.get("assertion") or {}).get("object", {}).get("id", "")
            ).removeprefix("grt:")
            if core:
                grants.append(
                    self._note(
                        f"grt:{core}", "grant", (g.get("summary") or {}).get("title") or core
                    )
                )
        metrics = d.get("metrics") or {}
        return {
            "ref": ref,
            "title": d.get("title"),
            "journal": d.get("journal"),
            "year": d.get("pub_year"),
            "authors": ", ".join(authors[:12]) + (" and others" if len(authors) > 12 else ""),
            "tessera_people": tessera[:12],
            "abstract": _cut(d.get("abstract")),
            "topics": [m.get("descriptor") for m in d.get("mesh") or [] if m.get("major_topic")][
                :10
            ],
            "grants": [g for g in grants if g],
            "retracted": bool(d.get("is_retracted")),
            "citations": metrics.get("citation_count"),
            "relative_citation_ratio": metrics.get("relative_citation_ratio"),
        }

    def _grant(self, d: dict[str, Any]) -> dict[str, Any]:
        ref = self._note(
            f"grt:{d.get('core_project_num')}",
            "grant",
            d.get("title"),
            f"{d.get('core_project_num')} · {d.get('institute')}",
        )
        years = [y.get("fiscal_year") for y in d.get("fiscal_years") or []]
        papers = [
            self._note(
                p.get("assertion", {}).get("subject", {}).get("id"),
                "publication",
                (p.get("summary") or {}).get("title"),
            )
            for p in d.get("publications") or []
        ]
        studies = [
            self._note(
                s.get("assertion", {}).get("subject", {}).get("id"),
                "study",
                (s.get("summary") or {}).get("title")
                or (s.get("summary") or {}).get("brief_title"),
            )
            for s in d.get("studies") or []
        ]
        return {
            "ref": ref,
            "title": d.get("title"),
            "activity": d.get("activity_code"),
            "institute": d.get("institute"),
            "start": d.get("project_start"),
            "end": d.get("project_end"),
            "active": d.get("is_active"),
            "fiscal_years": years,
            "total_awarded": (d.get("totals") or {}).get("total_awarded"),
            "investigators": self._people(d.get("investigators")),
            "abstract": _cut(d.get("abstract")),
            "terms": (d.get("terms") or [])[:12],
            "papers": [p for p in papers if p][:20],
            "studies": [s for s in studies if s][:10],
        }

    def _study(self, d: dict[str, Any]) -> dict[str, Any]:
        ref = self._note(f"stu:{d.get('nct_id')}", "study", d.get("brief_title"), d.get("nct_id"))
        officials = []
        for o in d.get("officials") or []:
            linked = _linked(o)
            for p in linked:
                self._note(p["ref"], "person", p["name"])
            officials.append(
                {
                    "name": o.get("name"),
                    "role": o.get("role"),
                    "tessera_person": linked[0] if linked else None,
                }
            )
        papers = [
            self._note(f"pub:{p.get('pmid')}", "publication", (p.get("summary") or {}).get("title"))
            for p in d.get("publications") or []
        ]
        return {
            "ref": ref,
            "title": d.get("brief_title"),
            "status": d.get("registry_status"),
            "phases": d.get("phases"),
            "type": d.get("study_type"),
            "conditions": d.get("conditions"),
            "interventions": [i.get("name") for i in d.get("interventions") or []],
            "sponsor": d.get("lead_sponsor"),
            "start": d.get("start_date"),
            "completion": d.get("completion_date"),
            "enrollment": d.get("enrollment"),
            "summary": _cut(d.get("brief_summary")),
            "officials": officials[:8],
            "papers": [p for p in papers if p][:15],
        }

    def _opportunity(self, d: dict[str, Any]) -> dict[str, Any]:
        ref = self._note(
            d.get("id"),
            "opportunity",
            d.get("title"),
            f"{d.get('number') or ''} · {d.get('sponsor') or ''}".strip(" ·"),
        )
        keys = (
            "number",
            "title",
            "sponsor",
            "status_label",
            "posted",
            "closes",
            "internal_deadline",
            "award_floor",
            "award_ceiling",
            "estimated_funding",
            "expected_awards",
            "cost_sharing",
            "applicant_type_labels",
            "activity_codes",
            "clinical_trial",
            "limited_submission",
        )
        return {
            "ref": ref,
            **{k: d.get(k) for k in keys if d.get(k) not in (None, [], "")},
            "description": _cut(d.get("description")),
        }

    def _group(self, slug: str) -> dict[str, Any]:
        g = self._get(f"/api/v1/groups/{quote(slug, safe='')}/overview")
        out = self._group_item(g)
        out["leaders"] = self._people(g.get("leaders"))
        out["sub_groups"] = [
            self._note(f"grp:{c['slug']}", "group", c.get("name"), c.get("kind_label"))
            for c in g.get("children") or []
            if c.get("slug")
        ]
        page = self._get(
            f"/api/v1/groups/{quote(slug, safe='')}/page"
        )  # published only, never a draft
        text = [
            f"{b.get('title') or ''}: {b.get('text') or ''}".strip(": ")
            for b in (page or {}).get("blocks") or []
            if b.get("type") in ("about", "news") and b.get("text")
        ]
        if text:
            out["page"] = _cut(" | ".join(text), 2000)
        return out

    # --- the tools ---------------------------------------------------------------------------

    def _tool_search(self, query: str, kind: str | None = None) -> dict[str, Any]:
        query = str(query)[:300]
        if kind == "group":
            groups = self._get("/api/v1/groups", {"q": query, "limit": 8}) or []
            return {"query": query, "groups": [self._group_item(g) for g in groups]}
        if kind:
            if kind not in ("person", "publication", "grant", "study", "opportunity"):
                raise ToolError(
                    "kind must be person, publication, grant, study, opportunity, or group, "
                    f"not {kind!r}."
                )
            items = self._get("/api/v1/search", {"q": query, "type": kind, "limit": 8}) or []
            return {"query": query, kind: [self._search_item(i) for i in items]}
        grouped = self._get("/api/v1/search", {"q": query, "per_type": 4}) or {}
        out: dict[str, Any] = {"query": query}
        for k, block in grouped.items():
            if block.get("items"):
                out[k] = {
                    "total": block.get("total"),
                    "items": [self._search_item(i) for i in block["items"]],
                }
        groups = self._get("/api/v1/groups", {"q": query, "limit": 3}) or []
        if groups:
            out["group"] = [self._group_item(g) for g in groups]
        return out

    def _tool_open_record(self, ref: str) -> dict[str, Any]:
        ref = self._ref(ref, "person", "publication", "grant", "study", "opportunity", "group")
        natural = quote(ref[4:] if ":" in ref[:4] else ref, safe="")
        kind = kind_of(ref)
        if kind == "group":
            return self._group(ref[4:])
        path = {
            "person": "people",
            "publication": "publications",
            "grant": "grants",
            "study": "studies",
            "opportunity": "opportunities",
        }[kind]
        data = self._get(f"/api/v1/{path}/{natural}")
        return getattr(self, f"_{kind}")(data)

    def _tool_experts(self, topic: str) -> dict[str, Any]:
        topic = str(topic)[:300]
        people = self._get("/api/v1/experts", {"q": topic, "limit": 10}) or []
        # Papers on the topic, so an answer can show the work and not only the names: each person
        # gets theirs among them.
        found = self._get("/api/v1/search", {"q": topic, "type": "publication", "limit": 12}) or []
        papers = [self._search_item(i) for i in found]
        out = []
        for p in people:
            ref = self._note(p.get("id"), "person", p.get("name"), ", ".join(p.get("units") or []))
            theirs = [
                {"ref": paper["ref"], "title": paper["title"], "year": paper.get("year")}
                for paper in papers
                if any(x["ref"] == ref for x in paper.get("tessera_people") or [])
            ]
            matches = p.get("matches") or []
            matched = [f"{m.get('descriptor')} ({m.get('papers')} papers)" for m in matches]
            out.append({"ref": ref, "name": p.get("name"), "units": p.get("units"),
                        "score": p.get("score"), "matched": matched[:4],
                        "papers_on_topic": theirs[:3]})  # fmt: skip
        return {"topic": topic, "people": out}

    def _tool_work_of(self, ref: str) -> dict[str, Any]:
        ref = self._ref(ref, "person", "group")
        if kind_of(ref) == "group":
            slug = quote(ref[4:], safe="")
            o = self._get(f"/api/v1/groups/{slug}/overview")
            self._note(ref, "group", o.get("name"), o.get("kind_label"))
            pubs = (
                self._get(
                    f"/api/v1/groups/{slug}/publications", {"status": "accepted", "limit": 15}
                )
                or []
            )
            return {
                "ref": ref,
                "name": o.get("name"),
                "people": [
                    {
                        "ref": self._note(p.get("person_id"), "person", p.get("name")),
                        "name": p.get("name"),
                        "role": p.get("role_label"),
                        "title": p.get("title"),
                    }
                    for p in o.get("people") or []
                ][:25],
                "topics": [t.get("descriptor") for t in o.get("topics") or []][:12],
                "confirmed_papers": (o.get("publications") or {}).get("accepted"),
                "recent_papers": [self._publication_line(p) for p in pubs],
                "grants": [
                    self._note(g.get("ref"), "grant", (g.get("summary") or {}).get("title"))
                    for g in o.get("grants") or []
                ][:15],
                "studies": [
                    self._note(s.get("ref"), "study", (s.get("summary") or {}).get("title"))
                    for s in o.get("studies") or []
                ][:10],
                "connected_groups": [
                    self._note(f"grp:{c['slug']}", "group", c.get("name"))
                    for c in o.get("connections") or []
                    if c.get("slug")
                ][:8],
            }
        pid = quote(ref, safe="")
        person = self._person(self._get(f"/api/v1/people/{pid}"))
        pubs = (
            self._get(f"/api/v1/people/{pid}/publications", {"status": "accepted", "limit": 15})
            or []
        )
        grants = self._get(f"/api/v1/people/{pid}/grants") or []
        studies = self._get(f"/api/v1/people/{pid}/studies") or []
        return {
            **person,
            "recent_papers": [self._publication_line(p) for p in pubs],
            "grants_list": [self._link_line(g, "grant") for g in grants][:15],
            "studies_list": [self._link_line(s, "study") for s in studies][:10],
        }

    def _publication_line(self, p: dict[str, Any]) -> dict[str, Any] | None:
        s = p.get("summary") or p
        pmid = s.get("pmid")
        if not pmid:
            return None
        return {
            "ref": self._note(f"pub:{pmid}", "publication", s.get("title"), s.get("journal")),
            "title": s.get("title"),
            "year": s.get("year"),
            "journal": s.get("journal"),
        }

    def _link_line(self, item: dict[str, Any], kind: str) -> dict[str, Any] | None:
        status = (item.get("assertion") or {}).get("status", item.get("status", "accepted"))
        if status != "accepted":
            return None
        s = item.get("summary") or item
        ref = (
            item.get("ref")
            or s.get("id")
            or (
                f"grt:{s['core_project_num']}"
                if s.get("core_project_num")
                else f"stu:{s['nct_id']}"
                if s.get("nct_id")
                else None
            )
        )
        title = s.get("title") or s.get("brief_title")
        return {
            "ref": self._note(ref, kind, title),
            "title": title,
            "status": s.get("registry_status") or ("active" if s.get("is_active") else None),
        }

    def _tool_insight_figure(
        self,
        metric: str,
        cohort: str = "organization",
        start: int | None = None,
        end: int | None = None,
    ) -> dict[str, Any]:
        if metric not in _METRICS:
            raise ToolError(f"There is no metric {metric!r}.")
        cohort = re.sub(r"^grp:", "group:", str(cohort or "organization").strip())
        d = self._get(
            f"/api/v1/insight/figures/{quote(metric, safe='')}",
            {"cohort": cohort, "start": start, "end": end, "basis": "confirmed"},
        )
        self.figures += 1
        params = {
            "cohort": d["cohort"].get("spec") or cohort,
            "start": d["period"].get("start"),
            "end": d["period"].get("end"),
            "basis": "confirmed",
        }
        ref = f"fig:{self.figures}"
        self._returned.append(ref)
        name = d["metric"].get("name")
        self.records[ref] = Record(
            ref,
            "figure",
            f"{name}: {d['cohort'].get('label')}, {d['period'].get('label')}",
            "Insight",
            INSIGHT_PAGES.get(d["metric"].get("area"), "/insight") + "?" + urlencode(params),
        )
        return {
            "ref": ref,
            "metric": name,
            "definition": _cut(d["metric"].get("definition"), 400),
            "cohort": d["cohort"].get("label"),
            "cohort_size": d["cohort"].get("size"),
            "period": d["period"].get("label"),
            "columns": [c.get("label") for c in d.get("columns") or []],
            "rows": (d.get("rows") or [])[:30],
            "totals": d.get("totals"),
            "notes": d.get("notes"),
        }

    def _tool_impact_thread(self, ref: str) -> dict[str, Any]:
        ref = self._ref(ref, "grant", "publication", "person")
        kind = kind_of(ref)
        natural = quote(ref[4:] if kind != "person" else ref, safe="")
        if kind == "person":
            if ref not in self.records:
                profile = self._get(f"/api/v1/people/{natural}")
                self._note(ref, "person", profile.get("name"))
            else:
                self._returned.append(ref)
            d = self._get(f"/api/v1/people/{natural}/impact")
            return {
                "ref": ref,
                "summary": d.get("papers"),
                "grants": [
                    {
                        "ref": self._note(g.get("id"), "grant", g.get("title")),
                        "title": g.get("title"),
                        "summary": g.get("summary"),
                    }
                    for g in d.get("grants") or []
                ][:15],
                "clinical_articles": [self._clinical(c) for c in d.get("clinical") or []][:15],
                "notes": d.get("notes"),
            }
        path = "grants" if kind == "grant" else "publications"
        d = self._get(f"/api/v1/{path}/{natural}/thread")
        head = d.get("grant") or d.get("paper") or {}
        self._note(ref, kind, head.get("title"))
        return {
            "ref": ref,
            "summary": d.get("summary"),
            "grants": [
                self._note(g.get("id"), "grant", g.get("title")) for g in d.get("grants") or []
            ][:15],
            "papers": [
                {
                    "ref": self._note(p.get("id"), "publication", p.get("title"), p.get("journal")),
                    "title": p.get("title"),
                    "year": p.get("year"),
                }
                for p in d.get("papers") or []
            ][:20],
            "studies": [
                {
                    "ref": self._note(s.get("id"), "study", s.get("title")),
                    "title": s.get("title"),
                    "status": s.get("status"),
                    "phases": s.get("phases"),
                }
                for s in d.get("studies") or []
            ][:10],
            "clinical_articles": [self._clinical(c) for c in d.get("clinical") or []][:15],
            "notes": d.get("notes"),
        }

    def _clinical(self, c: dict[str, Any]) -> dict[str, Any]:
        """A clinical article citing the papers. Most are not Tessera's records, so they are cited
        with a link to PubMed, where the thread's page links them too."""
        pmid = c.get("pmid")
        url = c.get("url") or (f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None)
        subtitle = " · ".join(
            str(x) for x in (c.get("kind_label"), c.get("journal"), c.get("year")) if x
        )
        ref = self._note(f"pub:{pmid}", "article", c.get("title"), subtitle, url) if pmid else None
        return {"ref": ref, "title": c.get("title"), "journal": c.get("journal"),
                "year": c.get("year"), "kind": c.get("kind_label")}  # fmt: skip

    def _tool_opportunity_fit(self, ref: str) -> dict[str, Any]:
        ref = self._ref(ref, "opportunity")
        natural = quote(ref[4:], safe="")
        opp = self._opportunity(self._get(f"/api/v1/opportunities/{natural}"))
        fit = self._get(f"/api/v1/opportunities/{natural}/fit") or {}
        eligibility = fit.get("eligibility") or {}
        return {
            **opp,
            "read_as": [c.get("descriptor") for c in fit.get("concepts") or []][:10],
            "people": [
                {
                    "ref": self._note(p.get("id"), "person", p.get("name")),
                    "name": p.get("name"),
                    "fit": p.get("band_label"),
                    "score": p.get("score"),
                    "matched": [m.get("descriptor") for m in p.get("matches") or []][:4],
                }
                for p in fit.get("people") or []
            ][:10],
            "groups": [
                {"name": g.get("name"), "fit": g.get("band_label")} for g in fit.get("groups") or []
            ][:5],
            "eligibility": {
                "overall": eligibility.get("label"),
                "checks": [
                    f"{c.get('label')}: {c.get('detail')}" for c in eligibility.get("checks") or []
                ],
            },
        }

    def _tool_opportunities_for(self, ref: str) -> dict[str, Any]:
        ref = self._ref(ref, "person", "group")
        if kind_of(ref) == "group":
            data = self._get(f"/api/v1/groups/{quote(ref[4:], safe='')}/opportunities")
        else:
            data = self._get(f"/api/v1/people/{quote(ref, safe='')}/opportunities")
        items = data.get("items") if isinstance(data, dict) else data
        return {
            "ref": ref,
            "opportunities": [
                {
                    "ref": self._note(
                        o.get("id") or o.get("ref"), "opportunity", o.get("title"), o.get("sponsor")
                    ),
                    "title": o.get("title"),
                    "sponsor": o.get("sponsor"),
                    "closes": o.get("closes"),
                    "fit": (o.get("fit") or {}).get("label"),
                    "eligibility": (o.get("eligibility") or {}).get("label"),
                }
                for o in items or []
            ][:12],
        }


__all__ = [
    "REF",
    "TOOLS",
    "Fetch",
    "Record",
    "ToolError",
    "Toolbox",
    "href_of",
    "kind_of",
    "normalize_ref",
]
