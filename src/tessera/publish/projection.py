"""What the public site may show: an allowlisted projection of the ledger, the lake, and the
search index (PLT-07).

Only people and groups that opted in (``public``) get pages. Every field below is chosen by
name; nothing is copied wholesale, so a field added elsewhere never reaches the public site by
accident. Publications appear as citations with links (title, authors, journal, year, DOI,
PubMed ID), never abstracts. Grants and studies appear by title, number, years, and status.
No email addresses of people, no internal IDs, no evidence, scores, or decisions.

A group's page follows its published blocks. Its people are listed by name and role; only
people who opted in link to a profile. "Writes most with" names only public groups.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from tessera.analytics.concepts import profile_path
from tessera.analytics.experts import person_concepts
from tessera.analytics.vocabulary import mesh_year, person_areas
from tessera.config import Config
from tessera.evidence.memberships import is_current
from tessera.groups import openings, pages, profile, store, themes
from tessera.home import DataHome
from tessera.lake import Lake
from tessera.ledger.tables import assertion, person, person_identifier, unit
from tessera.search.index import index_path
from tessera.search.query import SearchIndex

PUBLICATIONS_PER_PAGE = 200  # newest first; older ones are a link to PubMed away
CITATION_FIELDS = ("pmid", "title", "authors", "journal", "year", "doi", "is_retracted")
GRANT_FIELDS = ("core_project_num", "title", "institute", "fiscal_years", "is_active")
STUDY_FIELDS = ("nct_id", "title", "registry_status", "phases", "conditions")


def _day(value: Any) -> str | None:
    return f"{value.day} {value:%B %Y}" if value else None


def slugify(text: str) -> str:
    ascii_ = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_).strip("-")[:80].strip("-") or "page"


def _pick(summary: dict[str, Any] | None, fields: tuple[str, ...]) -> dict[str, Any] | None:
    if not summary:
        return None
    return {k: summary.get(k) for k in fields}


@dataclass
class Site:
    """Everything the public site shows, already projected."""

    title: str
    intro: str
    organization: str
    built: date
    mesh_year: int | None
    groups: list[dict[str, Any]] = field(default_factory=list)
    people: list[dict[str, Any]] = field(default_factory=list)
    kinds: list[tuple[str, str]] = field(default_factory=list)
    favicon: str = "assets/favicon.svg"  # paths within the site
    social_image: str | None = None
    footer_links: tuple[tuple[str, str], ...] = ()


def project(config: Config, home: DataHome, engine: Engine, today: date | None = None) -> Site:
    today = today or date.today()
    vocab = config.vocabulary
    org = config.organization
    site = Site(
        title=config.publish.title or org.name,
        intro=config.publish.intro,
        organization=org.name,
        built=today,
        mesh_year=mesh_year(home),
        kinds=list(vocab.kinds),
        footer_links=config.branding.footer_links,
    )
    brand = config.branding
    if brand.favicon and brand.favicon.is_file():
        site.favicon = f"assets/favicon{brand.favicon.suffix.lower()}"
    if brand.social_image and brand.social_image.is_file():
        site.social_image = f"assets/social{brand.social_image.suffix.lower()}"
    index = SearchIndex(index_path(home)) if index_path(home).is_file() else None

    def summaries(refs: list[str]) -> dict[str, dict[str, Any]]:
        if not refs or index is None:
            return {}
        return index.summaries(sorted(set(refs)))

    with engine.connect() as conn:
        public_groups = {
            r["id"]: dict(r)
            for r in conn.execute(
                select(unit).where(unit.c.public.is_(True), unit.c.status == "active")
            ).mappings()
        }
        public_people = {
            r.id: r
            for r in conn.execute(
                select(person.c.id, person.c.display_name, person.c.family_name).where(
                    person.c.public.is_(True), person.c.status.in_(("active", "former"))
                )
            )
        }
        # Addresses: from names, in a fixed order, so they are stable between builds.
        person_slug: dict[str, str] = {}
        taken: set[str] = set()
        for pid in sorted(public_people):
            base = slugify(public_people[pid].display_name)
            slug, n = base, 1
            while slug in taken:
                n += 1
                slug = f"{base}-{n}"
            taken.add(slug)
            person_slug[pid] = slug
        group_slug = {gid: g["slug"] for gid, g in public_groups.items()}
        # Approved plain-language summaries: only while their feature is on, and only of public
        # people and groups.
        lay: dict[str, dict[str, dict[str, Any]]] = {"person": {}, "group": {}}
        if config.ai.enabled:
            from tessera.ai import summaries as summary_store

            for kind, ids in (("person", public_people), ("group", public_groups)):
                lay[kind] = {
                    sid: {"text": r["text"], "approved_by": r["decided_by"],
                          "approved_on": _day(r["decided_at"]), "label": summary_store.LABEL}
                    for sid, r in summary_store.approved(conn, kind, sorted(ids)).items()
                }  # fmt: skip
        orcid = dict(
            conn.execute(
                select(person_identifier.c.person_id, person_identifier.c.value).where(
                    person_identifier.c.scheme == "orcid",
                    person_identifier.c.status == "accepted",
                    person_identifier.c.person_id.in_(sorted(public_people)),
                )
            ).all()
        )
        memberships: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in store.memberships(conn, person_ids=sorted(public_people)):
            if r["object_id"] in public_groups and is_current(r["qualifiers"], today):
                view = store.membership_view(r, vocab, today)
                g = public_groups[r["object_id"]]
                memberships[r["subject_id"]].append(
                    {
                        "group": g["name"],
                        "slug": g["slug"],
                        "kind": vocab.kind_label(g["kind"]),
                        "role": view["role_label"],
                        "title": view["title"],
                    }
                )
        authored: dict[str, list[str]] = defaultdict(list)
        grants: dict[str, list[str]] = defaultdict(list)
        studies: dict[str, list[str]] = defaultdict(list)
        for subject, predicate, obj in conn.execute(
            select(assertion.c.subject_id, assertion.c.predicate, assertion.c.object_id).where(
                assertion.c.subject_id.in_(sorted(public_people)),
                assertion.c.predicate.in_(("authored", "investigator_on", "official_of")),
                assertion.c.status == "accepted",
                assertion.c.reported.is_(True),
            )
        ):
            {"authored": authored, "investigator_on": grants, "official_of": studies}[predicate][
                subject
            ].append(obj)

        group_data: dict[str, dict[str, Any]] = {}
        for gid in public_groups:
            current, alumni = profile.people_of(conn, gid, config, today)
            ids = sorted({v["person_id"] for v in current})
            blocks, _ = pages.published_blocks(conn, gid)
            group_data[gid] = {
                "current": current,
                "alumni": alumni,
                "ids": ids,
                "blocks": blocks,
                "attributed": [a["subject_id"] for a in profile.attributions(conn, gid)],
                "grants": profile.linked(conn, ids, "investigator_on"),
                "studies": profile.linked(conn, ids, "official_of"),
                "connections": [
                    c
                    for c in profile.connections(conn, gid, ids, config, today)
                    if c["id"] in public_groups
                ],
                "parents": [
                    p
                    for p in store.parents_of(conn, [gid]).get(gid, [])
                    if p["id"] in public_groups
                ],
                "children": [c for c in store.children_of(conn, gid) if c["id"] in public_groups],
                "openings": openings.list_openings(conn, [gid], today),
                "themes": themes.list_themes(conn, gid),
            }

    # --- people ---------------------------------------------------------------------------
    with Lake(home.lake) as lake:
        for pid in sorted(public_people, key=lambda p: (
            (public_people[p].family_name or public_people[p].display_name).lower(), p)):  # fmt: skip
            papers = summaries(authored.get(pid, []))
            citations = sorted(
                (c for c in (_pick(papers.get(r), CITATION_FIELDS) for r in authored.get(pid, []))
                 if c),
                key=lambda c: (-(c["year"] or 0), c["title"] or ""),
            )[:PUBLICATIONS_PER_PAGE]  # fmt: skip
            site.people.append(
                {
                    "slug": person_slug[pid],
                    "name": public_people[pid].display_name,
                    "summary": lay["person"].get(pid),
                    "sort": (public_people[pid].family_name or public_people[pid].display_name),
                    "orcid": orcid.get(pid),
                    "groups": sorted(memberships.get(pid, []), key=lambda m: m["group"]),
                    "areas": [
                        {"area": a["area"], "share": a["share"]}
                        for a in person_areas(lake, home, pid)[:5]
                        if a["share"] >= 0.03
                    ],
                    "topics": [
                        c["descriptor"] for c in person_concepts(lake, profile_path(home), pid, 10)
                    ],
                    "publications": citations,
                    "grants": [
                        g for g in (_pick(s, GRANT_FIELDS) for s in summaries(grants.get(pid, [])).values()) if g
                    ],
                    "studies": [
                        s for s in (_pick(x, STUDY_FIELDS) for x in summaries(studies.get(pid, [])).values()) if s
                    ],
                }
            )  # fmt: skip

    # --- groups ---------------------------------------------------------------------------
    for gid, g in sorted(public_groups.items(), key=lambda kv: kv[1]["name"].lower()):
        data = group_data[gid]
        topics, areas = profile.topics_and_areas(home, data["ids"])

        def person_ref(v: dict[str, Any]) -> dict[str, Any]:
            return {
                "name": v["name"],
                "slug": person_slug.get(v["person_id"]),
                "role": v["role_label"],
                "leader": v["leader"],
                "title": v["title"],
                "start": v["start"],
                "end": v["end"],
            }

        pubs = summaries(data["attributed"])
        citations = sorted(
            (c for c in (_pick(pubs.get(r), CITATION_FIELDS) for r in data["attributed"]) if c),
            key=lambda c: (-(c["year"] or 0), c["title"] or ""),
        )[:PUBLICATIONS_PER_PAGE]
        by_year: dict[int, int] = defaultdict(int)
        for c in citations:
            if c["year"]:
                by_year[c["year"]] += 1
        grant_list = [
            x
            for x in (_pick(s, GRANT_FIELDS) for s in summaries(list(data["grants"])).values())
            if x
        ]
        grant_list.sort(key=lambda x: -((x["fiscal_years"] or [0, 0])[1] or 0))
        study_list = [
            x
            for x in (_pick(s, STUDY_FIELDS) for s in summaries(list(data["studies"])).values())
            if x
        ]
        featured_refs = [
            f"pub:{p}" for b in data["blocks"] if b["type"] == "publications"
            for p in b.get("featured", [])
        ]  # fmt: skip
        featured = {
            ref.removeprefix("pub:"): _pick(s, CITATION_FIELDS)
            for ref, s in summaries([r for r in featured_refs if r in data["attributed"]]).items()
        }
        theme_refs = [r for t in data["themes"] for r in t["links"]]
        theme_summaries = summaries([r for r in theme_refs if not r.startswith("per_")])
        names = {v["person_id"]: v["name"] for v in [*data["current"], *data["alumni"]]}

        def theme_link(
            ref: str,
            names: dict[str, str] = names,
            theme_summaries: dict[str, dict[str, Any]] = theme_summaries,
        ) -> dict[str, Any] | None:
            if ref.startswith("per_"):
                if ref not in names and ref not in public_people:
                    return None
                name = names.get(ref) or public_people[ref].display_name
                return {"kind": "person", "label": name, "slug": person_slug.get(ref)}
            s = theme_summaries.get(ref) or {}
            if ref.startswith("pub:"):
                return {"kind": "publication", "label": s.get("title") or ref[4:],
                        "pmid": ref[4:], "doi": s.get("doi")}  # fmt: skip
            if ref.startswith("stu:"):
                return {"kind": "study", "label": s.get("title") or ref[4:], "nct_id": ref[4:]}
            return {"kind": "grant", "label": s.get("title") or ref[4:], "number": ref[4:]}

        site.groups.append(
            {
                "slug": group_slug[gid],
                "name": g["name"],
                "short_name": g["short_name"],
                "kind": g["kind"],
                "kind_label": config.vocabulary.kind_label(g["kind"]),
                "description": g["description"],
                "email": g["email"],
                "website": g["website"],
                "parents": [{"name": p["name"], "slug": group_slug[p["id"]]} for p in data["parents"]],
                "children": [
                    {"name": c["name"], "slug": group_slug[c["id"]],
                     "kind": config.vocabulary.kind_label(c["kind"])}
                    for c in data["children"]
                ],
                "blocks": data["blocks"],
                "summary": lay["group"].get(gid),
                "leaders": [person_ref(v) for v in data["current"] if v["leader"]],
                "people": [person_ref(v) for v in data["current"]],
                "alumni": [person_ref(v) for v in data["alumni"]],
                "publications": citations,
                "featured": featured,
                "by_year": sorted(by_year.items()),
                "grants": grant_list,
                "studies": study_list,
                "topics": [t["descriptor"] for t in topics],
                "areas": [{"area": a["area"], "share": a["share"]} for a in areas],
                "connections": [
                    {"name": c["name"], "slug": c["slug"], "papers": c["papers"]}
                    for c in data["connections"]
                ],
                "openings": [
                    {k: o[k] for k in ("title", "kind_label", "description", "url", "contact",
                                       "closes_on")}
                    for o in data["openings"]
                ],
                "themes": [
                    {"name": t["name"], "kind": t["kind_label"], "summary": t["summary"],
                     "links": [x for x in (theme_link(r) for r in t["links"]) if x]}
                    for t in data["themes"]
                ],
            }
        )  # fmt: skip
    return site


__all__ = ["Site", "project", "slugify"]
