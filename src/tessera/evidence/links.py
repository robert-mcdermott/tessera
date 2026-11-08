"""Links stated by public records, and inferred person-study links.

Source-reported (origin ``source``, status ``accepted``):
  acknowledges     publication -> grant   (RePORTER link table, PubMed grant list)
  references       study -> publication   (registry reference list)
  funded_by        study -> grant         (registry NIH secondary IDs)
  investigator_on  person -> grant        (RePORTER investigators, by NIH profile ID)

Inferred (origin ``inferred``, status ``suggested``):
  official_of      person -> study        (official's name, affiliation, and context)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.engine import Engine

from tessera.config import Config
from tessera.evidence import store
from tessera.evidence.authorship import WEIGHTS, sigmoid
from tessera.evidence.names import Match, best_match, family_keys, parse_display_name
from tessera.evidence.orgmatch import OrgMatcher
from tessera.lake import Lake

LINKS_METHOD = "links/v1"
OFFICIALS_METHOD = "officials/v1"


def _item(
    feature: str, value: Any, explanation: str, source_ref: str, weight: float | None = None
) -> dict[str, Any]:
    return {
        "feature": feature,
        "value": value,
        "weight": weight,
        "explanation": explanation,
        "source_ref": source_ref,
    }


@dataclass
class LinkStats:
    acknowledges: dict[str, int] = field(default_factory=dict)
    references: dict[str, int] = field(default_factory=dict)
    funded_by: dict[str, int] = field(default_factory=dict)
    investigator_on: dict[str, int] = field(default_factory=dict)
    official_of: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return dict(vars(self))


def _grants_in_lake(lake: Lake) -> tuple[set[str], dict[str, str]]:
    cores = {
        r["core_project_num"]
        for r in lake.rows(
            "SELECT DISTINCT core_project_num FROM reporter_project "
            "WHERE core_project_num IS NOT NULL"
        )
    }
    by_serial: dict[str, str] = {}
    ambiguous: set[str] = set()
    for core in cores:
        serial = core[3:]
        if serial in by_serial and by_serial[serial] != core:
            ambiguous.add(serial)  # e.g. an R01 and an R37 with the same serial
        by_serial[serial] = core
    for serial in ambiguous:
        by_serial.pop(serial, None)
    return cores, by_serial


def sync_source_links(engine: Engine, lake: Lake, stats: LinkStats) -> None:
    cores, by_serial = _grants_in_lake(lake)

    # publication -> grant
    evidence: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in lake.rows("SELECT pmid, core_project_num, appl_id FROM reporter_publication_link"):
        if r["core_project_num"] in cores:
            evidence[(r["pmid"], r["core_project_num"])].append(
                _item(
                    "reporter_link",
                    r["core_project_num"],
                    f"NIH RePORTER links this publication to {r['core_project_num']}",
                    f"reporter:link:{r['pmid']}:{r['core_project_num']}",
                )
            )
    for r in lake.rows("SELECT pmid, grant_id_raw, core_project_num, ic_serial FROM pubmed_grant"):
        core = r["core_project_num"] if r["core_project_num"] in cores else None
        via_serial = False
        if core is None and r["ic_serial"] in by_serial:
            core, via_serial = by_serial[r["ic_serial"]], True
        if core:
            how = " (matched by institute and serial number)" if via_serial else ""
            evidence[(r["pmid"], core)].append(
                _item(
                    "pubmed_grant_list",
                    r["grant_id_raw"],
                    f"The publication's PubMed record acknowledges “{r['grant_id_raw']}”{how}",
                    f"pubmed:{r['pmid']}#grants",
                )
            )
    drafts = [
        store.Draft(
            "publication",
            f"pub:{pmid}",
            "acknowledges",
            "grant",
            f"grt:{core}",
            origin="source",
            method=LINKS_METHOD,
            evidence=_dedupe(items),
            status="accepted",
        )
        for (pmid, core), items in evidence.items()
    ]
    stats.acknowledges = store.sync_assertions(
        engine, "acknowledges", drafts, origin="source"
    ).to_json()

    # study -> publication
    drafts = [
        store.Draft(
            "study",
            f"stu:{r['nct_id']}",
            "references",
            "publication",
            f"pub:{r['pmid']}",
            origin="source",
            method=LINKS_METHOD,
            status="accepted",
            qualifiers={"reference_type": r["type"]},
            evidence=[
                _item(
                    "registry_reference",
                    r["type"],
                    f"The registry record lists this publication "
                    f"({(r['type'] or 'reference').lower()})",
                    f"ctgov:{r['nct_id']}#references",
                )
            ],
        )
        for r in lake.rows("SELECT nct_id, pmid, type FROM ctgov_reference")
    ]
    stats.references = store.sync_assertions(
        engine, "references", drafts, origin="source"
    ).to_json()

    # study -> grant
    found: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in lake.rows(
        "SELECT nct_id, id, core_project_num, ic_serial FROM ctgov_secondary_id "
        "WHERE core_project_num IS NOT NULL OR ic_serial IS NOT NULL"
    ):
        core = (
            r["core_project_num"]
            if r["core_project_num"] in cores
            else by_serial.get(r["ic_serial"])
        )
        if core:
            found[(r["nct_id"], core)].append(
                _item(
                    "registry_secondary_id",
                    r["id"],
                    f"The registry record lists NIH grant “{r['id']}”",
                    f"ctgov:{r['nct_id']}#secondary_ids",
                )
            )
    drafts = [
        store.Draft(
            "study",
            f"stu:{nct}",
            "funded_by",
            "grant",
            f"grt:{core}",
            origin="source",
            method=LINKS_METHOD,
            evidence=_dedupe(items),
            status="accepted",
        )
        for (nct, core), items in found.items()
    ]
    stats.funded_by = store.sync_assertions(engine, "funded_by", drafts, origin="source").to_json()


def sync_investigators(engine: Engine, lake: Lake, stats: LinkStats) -> None:
    people = store.load_people(engine, ("active", "former", "candidate"))
    by_profile = {int(v): p.id for p in people for v in p.ids("nih_profile") if v.isdigit()}
    rows = lake.rows(
        "SELECT i.profile_id, i.is_contact_pi, p.core_project_num, p.fiscal_year, p.appl_id "
        "FROM reporter_investigator i JOIN reporter_project p USING (appl_id) "
        "WHERE p.core_project_num IS NOT NULL ORDER BY p.fiscal_year"
    )
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        person_id = by_profile.get(r["profile_id"])
        if person_id:
            grouped[(person_id, r["core_project_num"])].append(r)
    drafts = []
    for (person_id, core), records in grouped.items():
        years = sorted({r["fiscal_year"] for r in records if r["fiscal_year"]})
        contact = any(r["is_contact_pi"] for r in records)
        role = "contact PI" if contact else "PI"
        span = f"FY{years[0]}" + (f"-{years[-1]}" if len(years) > 1 else "") if years else ""
        drafts.append(
            store.Draft(
                "person",
                person_id,
                "investigator_on",
                "grant",
                f"grt:{core}",
                origin="source",
                method=LINKS_METHOD,
                status="accepted",
                qualifiers={"role": "contact_pi" if contact else "pi", "fiscal_years": years},
                evidence=[
                    _item(
                        "reporter_investigator",
                        role,
                        f"NIH RePORTER lists this person as {role} on {core} {span}".strip(),
                        f"reporter:{records[-1]['appl_id']}",
                    )
                ],
            )
        )
    stats.investigator_on = store.sync_assertions(
        engine, "investigator_on", drafts, origin="source"
    ).to_json()


def resolve_officials(
    engine: Engine, lake: Lake, config: Config, matcher: OrgMatcher, stats: LinkStats
) -> None:
    """Suggest which people are the officials named on studies."""
    people = store.load_people(engine, ("active", "former"))
    index: dict[str, list[store.PersonRecord]] = defaultdict(list)
    for record in people:
        for name in record.names:
            for k in family_keys(name.family):
                if record not in index[k]:
                    index[k].append(record)
    grants_of: dict[str, set[str]] = defaultdict(set)
    for row in store.assertions_for(engine, predicate="investigator_on"):
        grants_of[row["subject_id"]].add(row["object_id"].removeprefix("grt:"))
    study_grants: dict[str, set[str]] = defaultdict(set)
    for row in store.assertions_for(engine, predicate="funded_by"):
        study_grants[row["subject_id"].removeprefix("stu:")].add(
            row["object_id"].removeprefix("grt:")
        )
    involved = {
        r["nct_id"]
        for r in lake.rows(
            "SELECT s.nct_id, s.lead_sponsor, s.collaborators, "
            "list(t.facility) AS facilities FROM ctgov_study s "
            "LEFT JOIN ctgov_site t USING (nct_id) GROUP BY ALL"
        )
        if matcher.matches(r["lead_sponsor"])
        or any(matcher.matches(c) for c in r["collaborators"] or [])
        or any(matcher.matches(f) for f in r["facilities"] or [] if f)
    }

    drafts = []
    for r in lake.rows("SELECT nct_id, seq, name, affiliation, role FROM ctgov_official"):
        name = parse_display_name(r["name"])
        if name is None:
            continue
        ref = f"ctgov:{r['nct_id']}#officials{r['seq']}"
        seen: set[str] = set()
        for k in family_keys(name.family):
            for record in index.get(k, []):
                if record.id in seen:
                    continue
                seen.add(record.id)
                match = best_match(record.names, name)
                if match is Match.NONE:
                    continue
                features: list[dict[str, Any]] = []
                total = WEIGHTS["prior"]
                features.append(
                    _item(
                        "prior",
                        None,
                        "Starting point: a matching name on its own is weak evidence",
                        ref,
                        WEIGHTS["prior"],
                    )
                )
                if match is Match.FULL:
                    total += WEIGHTS["given_full_match"]
                    features.append(
                        _item(
                            "given_full_match",
                            name.given,
                            f"Full first name matches ({name.given})",
                            ref,
                            WEIGHTS["given_full_match"],
                        )
                    )
                elif match is Match.MIDDLE_CONFLICT:
                    total += WEIGHTS["middle_conflict"]
                    features.append(
                        _item(
                            "middle_conflict",
                            name.initials,
                            "Middle initial differs",
                            ref,
                            WEIGHTS["middle_conflict"],
                        )
                    )
                elif match is Match.GIVEN_CONFLICT:
                    total += WEIGHTS["given_conflict"]
                    features.append(
                        _item(
                            "given_conflict",
                            name.given,
                            "First name differs",
                            ref,
                            WEIGHTS["given_conflict"],
                        )
                    )
                if matcher.matches(r["affiliation"]):
                    total += WEIGHTS["org_affiliation"]
                    features.append(
                        _item(
                            "org_affiliation",
                            r["affiliation"],
                            f"The official's affiliation matches {matcher.name}",
                            ref,
                            WEIGHTS["org_affiliation"],
                        )
                    )
                if r["nct_id"] in involved:
                    total += 1.0
                    features.append(
                        _item(
                            "org_study",
                            None,
                            f"{matcher.name} sponsors or hosts this study",
                            f"ctgov:{r['nct_id']}",
                            1.0,
                        )
                    )
                shared = sorted(grants_of[record.id] & study_grants[r["nct_id"]])
                if shared:
                    total += WEIGHTS["grant_link"]
                    features.append(
                        _item(
                            "grant_link",
                            shared[0],
                            f"The study cites {shared[0]}, a grant this person holds",
                            f"grant:{shared[0]}",
                            WEIGHTS["grant_link"],
                        )
                    )
                score = round(sigmoid(total), 4)
                drafts.append(
                    store.Draft(
                        "person",
                        record.id,
                        "official_of",
                        "study",
                        f"stu:{r['nct_id']}",
                        origin="inferred",
                        method=OFFICIALS_METHOD,
                        score=score,
                        evidence=sorted(features, key=lambda i: -abs(i["weight"] or 0)),
                        qualifiers={"role": r["role"], "official_name": r["name"]},
                    )
                )
    stats.official_of = store.sync_assertions(
        engine,
        "official_of",
        drafts,
        origin="inferred",
        min_score_for_new=config.evidence.store_threshold,
    ).to_json()


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for item in items:
        marker = (item["feature"], item["value"])
        if marker not in seen:
            seen.add(marker)
            out.append(item)
    return out


__all__ = ["LinkStats", "resolve_officials", "sync_investigators", "sync_source_links"]
