"""Insight (wave 4): metrics with versions, cohorts, periods, and figures that say what they
count (INT-01); counting rules that never count a paper twice; small numbers marked."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pyarrow as pa
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tessera.api.app import create_app
from tessera.config import load_config
from tessera.demo import build_demo
from tessera.groups import store as groups
from tessera.insight import cohorts, metrics
from tessera.insight.cohorts import MEMBERS_SCHEMA, CohortSpec
from tessera.insight.figure import Period, PeriodError
from tessera.insight.inputs import SCHEMAS, Inputs
from tessera.insight.run import compute
from tessera.lake import Lake
from tessera.ledger.db import make_engine
from tessera.ledger.tables import assertion, person

TODAY = date(2026, 11, 7)


# --- pure pieces ------------------------------------------------------------------------------


def test_metrics_have_versions_and_check_their_options() -> None:
    found = metrics.get("publications.count")
    assert found.ref == "publications.count/v1"
    assert found.resolve_options({}) == {"basis": "likely"}
    assert found.resolve_options({"basis": "confirmed", "ignored": "x"}) == {"basis": "confirmed"}
    with pytest.raises(metrics.MetricError, match="not one of likely, confirmed"):
        found.resolve_options({"basis": "everything"})
    with pytest.raises(metrics.MetricError, match="no metric"):
        metrics.get("people.ranked")
    listed = [m.to_json() for m in metrics.all_metrics()]
    assert all(m["definition"] and m["counting"] and m["sources"] for m in listed)


def test_periods_default_to_ten_years_or_the_complete_ones() -> None:
    assert Period.make(None, None, today=TODAY).label == "2017\u20132026"
    assert Period.make(None, None, today=TODAY, earliest=2022).label == "2022\u20132026"
    assert Period.make(2020, 2020, "fiscal", today=TODAY).label == "FY2020"
    for start, end in ((2025, 2020), (1900, 2000), (1960, 2026)):
        with pytest.raises(PeriodError):
            Period.make(start, end, today=TODAY)


def test_cohort_specifications_round_trip() -> None:
    for text in ("organization", "group:lab-a+below", "group:lab-a;current", "people:per_1,per_2",
                 "search:t cells"):  # fmt: skip
        spec = cohorts.parse(text)
        assert isinstance(spec, CohortSpec) and spec.text() == text
        assert CohortSpec.from_json(spec.to_json()) == spec
    assert cohorts.parse("coh_0123") == "coh_0123"
    for bad in ("people:", "group:", "everyone", "search:  "):
        with pytest.raises(cohorts.CohortError):
            cohorts.parse(bad)


def inputs(members: list[dict], links: list[dict], papers: list[dict]) -> Inputs:
    return Inputs(
        {
            "members": pa.Table.from_pylist(members, schema=MEMBERS_SCHEMA),
            "paper_links": pa.Table.from_pylist(links, schema=SCHEMAS["paper_links"]),
            "papers": pa.Table.from_pylist(papers, schema=SCHEMAS["papers"]),
        },
        [{"source": "pubmed", "label": "PubMed", "last_success_at": "2026-11-07T03:00:00Z"}],
        datetime(2026, 11, 7, tzinfo=UTC),
    )


def paper(pmid: str, year: int, retracted: bool = False) -> dict:
    return {"pmid": pmid, "year": year, "title": f"Paper {pmid}", "journal": "J", "doi": None,
            "retracted": retracted}  # fmt: skip


def link(person_id: str, pmid: str, confirmed: bool = True) -> dict:
    return {"person_id": person_id, "pmid": pmid, "confirmed": confirmed, "score": 0.99,
            "assertion_id": f"asr_{person_id}_{pmid}"}  # fmt: skip


def by_year(data: Inputs, basis: str = "likely", start: int = 2020, end: int = 2026) -> dict:
    figure = compute(
        metrics.get("publications.count"), data, {"label": "Lab", "size": 2},
        Period(start, end), {"basis": basis}, small_number=5, today=TODAY,
    )  # fmt: skip
    return figure.to_json(records=True)


def test_a_paper_counts_once_in_its_year_and_only_while_its_author_was_a_member() -> None:
    data = inputs(
        [{"person_id": "a", "name": "Ada", "start_year": None, "end_year": None},
         {"person_id": "b", "name": "Ben", "start_year": 2024, "end_year": None}],
        [link("a", "1"), link("b", "1"), link("b", "2"), link("b", "3"), link("a", "4", False),
         link("a", "5"), link("c", "6")],
        [paper("1", 2024), paper("2", 2023), paper("3", 2025), paper("4", 2025),
         paper("5", 2025, retracted=True), paper("6", 2025)],
    )  # fmt: skip
    out = by_year(data)
    rows = {r["year"]: r for r in out["rows"]}
    # 1: two members, counted once; 2: Ben joined in 2024; 4: likely; 5: retracted; 6: not ours
    assert rows[2024] == {"year": 2024, "papers": 1, "confirmed": 1, "likely": 0}
    assert rows[2023]["papers"] == 0
    assert rows[2025] == {"year": 2025, "papers": 2, "confirmed": 1, "likely": 1}
    assert out["totals"] == {"papers": 3, "confirmed": 2, "likely": 1}
    record = next(r for r in out["records"] if r["pmid"] == "1")
    assert record["members"] == ["Ada", "Ben"] and len(record["links"]) == 2
    confirmed_only = by_year(data, "confirmed")
    assert confirmed_only["totals"] == {"papers": 2, "confirmed": 2}
    assert "Only confirmed papers are counted." in confirmed_only["notes"]


def test_small_numbers_are_marked_and_the_figure_says_what_it_is() -> None:
    data = inputs(
        [{"person_id": "a", "name": "Ada", "start_year": None, "end_year": None}],
        [link("a", str(n)) for n in range(1, 9)],
        [paper(str(n), 2025 if n <= 6 else 2026) for n in range(1, 9)],
    )
    out = by_year(data, start=2025, end=2026)
    assert out["small"] == [{"row": 1, "columns": ["papers", "confirmed"]}]  # 2026: two papers
    assert out["metric"]["ref"] == "publications.count/v1"
    assert out["period"] == {"start": 2025, "end": 2026, "basis": "calendar",
                             "label": "2025\u20132026"}  # fmt: skip
    assert out["sources"][0]["last_success_at"] == "2026-11-07T03:00:00Z"
    assert "2026 is not over; its numbers will grow." in out["notes"]


# --- on the demo organization ----------------------------------------------------------------


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[object, TestClient]]:
    path = tmp_path_factory.mktemp("insight-demo")
    result = build_demo(path, today=TODAY)
    config = load_config(Path(result["config"]), env={})
    with TestClient(create_app(config), base_url="http://localhost") as client:
        yield config, client


def figure(client: TestClient, metric: str = "publications.count", **params: object) -> dict:
    response = client.get(f"/api/v1/insight/figures/{metric}", params=params)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_the_organizations_papers_are_its_members_distinct_papers(demo) -> None:  # type: ignore[no-untyped-def]
    config, client = demo
    out = figure(client, start=2022, end=2026)
    engine = make_engine(config.ledger_url)
    lake = Lake(config.data.home / "lake")
    try:
        with engine.connect() as conn:
            directory = set(
                conn.execute(
                    select(person.c.id).where(person.c.status.in_(("active", "former")))
                ).scalars()
            )
            rows = conn.execute(
                select(assertion.c.subject_id, assertion.c.object_id, assertion.c.status,
                       assertion.c.score)
                .where(assertion.c.predicate == "authored", assertion.c.reported.is_(True))
            ).all()  # fmt: skip
        strong = config.evidence.strong_threshold
        pmids = {
            r.object_id.removeprefix("pub:") for r in rows
            if r.subject_id in directory
            and (r.status == "accepted" or (r.status == "suggested" and (r.score or 0) >= strong))
        }  # fmt: skip
        sql = "SELECT pmid, pub_year FROM pubmed_article WHERE NOT coalesce(is_retracted, false)"
        years = {r["pmid"]: r["pub_year"] for r in lake.rows(sql)}
        expected = {p for p in pmids if 2022 <= (years.get(p) or 0) <= 2026}
    finally:
        lake.close()
        engine.dispose()
    assert out["totals"]["papers"] == len(expected) > 300
    assert sum(r["papers"] for r in out["rows"]) == out["totals"]["papers"]
    assert out["cohort"]["label"] == "The organization" and out["cohort"]["size"] > 80
    assert out["sources"][0]["label"] == "PubMed" and out["sources"][0]["last_success_at"]


def test_a_groups_figure_counts_members_work_while_they_were_members(demo) -> None:  # type: ignore[no-untyped-def]
    config, client = demo
    engine = make_engine(config.ledger_url)
    try:
        with engine.begin() as conn:
            lab = groups.resolve(conn, "cancer-immunotherapy-program")
            member = groups.memberships(conn, group_ids=[lab["id"]])[0]["subject_id"]
            gid = groups.create_group(
                conn, {"name": "Window Test Team", "kind": "team"}, actor="test",
                vocabulary=config.vocabulary,
            )  # fmt: skip
            groups.add_membership(
                conn, person_id=member, group_id=gid, role="member", start="2025-03",
                actor="test", vocabulary=config.vocabulary,
            )  # fmt: skip
            slug = groups.get_group(conn, gid)["slug"]
    finally:
        engine.dispose()
    everything = figure(client, cohort=f"people:{member}", start=2022, end=2026)
    windowed = figure(client, cohort=f"group:{slug}", start=2022, end=2026)
    current = figure(client, cohort=f"group:{slug};current", start=2022, end=2026)
    assert windowed["cohort"]["dated"] == 1 and "joined" in windowed["cohort"]["note"]
    years = {r["year"]: r["papers"] for r in windowed["rows"]}
    assert years[2022] == years[2023] == years[2024] == 0
    assert windowed["totals"]["papers"] == sum(
        r["papers"] for r in everything["rows"] if r["year"] >= 2025
    )
    assert current["totals"]["papers"] == everything["totals"]["papers"]


def test_figures_refuse_what_they_cannot_count(demo) -> None:  # type: ignore[no-untyped-def]
    _, client = demo
    cases = {
        "/api/v1/insight/figures/people.ranked": 404,
        "/api/v1/insight/figures/publications.count?basis=all": 400,
        "/api/v1/insight/figures/publications.count?start=2026&end=2020": 400,
        "/api/v1/insight/figures/publications.count?cohort=group:no-such-group": 404,
        "/api/v1/insight/figures/publications.count?cohort=coh_missing": 404,
        "/api/v1/insight/figures/publications.count?cohort=everyone": 400,
    }
    for path, status in cases.items():
        assert client.get(path).status_code == status, path


def test_records_say_why_each_paper_is_counted(demo) -> None:  # type: ignore[no-untyped-def]
    _, client = demo
    out = figure(client, cohort="group:cancer-immunotherapy-program", start=2024, end=2024,
                 records="true")  # fmt: skip
    assert out["record_count"] == len(out["records"]) == out["totals"]["papers"] > 0
    first = out["records"][0]
    assert first["status"] in ("confirmed", "likely") and first["members"]
    assert all(link.startswith("asr_") for link in first["links"])
    assert [c["key"] for c in out["record_columns"]][:2] == ["pmid", "year"]


def test_cohorts_can_be_saved_used_renamed_and_deleted(demo) -> None:  # type: ignore[no-untyped-def]
    _, client = demo
    made = client.post(
        "/api/v1/insight/cohorts",
        json={
            "name": "Immunotherapy and below",
            "cohort": "group:cancer-immunotherapy-program+below",
        },
    )
    assert made.status_code == 200, made.text
    saved = made.json()["data"]
    assert saved["id"].startswith("coh_") and saved["definition"]["below"] is True
    again = client.post("/api/v1/insight/cohorts", json={"name": saved["name"],
                                                         "cohort": "organization"})  # fmt: skip
    assert again.status_code == 400
    out = figure(client, cohort=saved["id"], start=2022, end=2026)
    assert out["cohort"]["label"] == saved["name"] and out["cohort"]["saved"]["id"] == saved["id"]
    renamed = client.post(f"/api/v1/insight/cohorts/{saved['id']}", json={"name": "CIP"})
    assert renamed.json()["data"]["name"] == "CIP"
    preview = client.get("/api/v1/insight/cohort", params={"cohort": saved["id"]}).json()["data"]
    assert preview["label"] == "CIP" and len(preview["members"]) == preview["size"]
    assert client.delete(f"/api/v1/insight/cohorts/{saved['id']}").status_code == 200
    assert client.get("/api/v1/insight/cohorts").json()["data"] == []


def test_years_and_definitions_are_offered(demo) -> None:  # type: ignore[no-untyped-def]
    _, client = demo
    years = client.get("/api/v1/insight/years").json()["data"]
    assert years["calendar"]["default"]["end"] >= years["calendar"]["default"]["start"]
    listed = client.get("/api/v1/insight/metrics").json()["data"]
    assert "publications.count/v1" in {m["ref"] for m in listed}


# --- funding ----------------------------------------------------------------------------------


def award(appl: int, core: str, fy: int, total: int | None, *, sub: str | None = None,
          kind: str = "5", ours: bool = True, institute: str = "NCI", activity: str = "R01",
          end: date | None = date(2028, 6, 30)) -> dict:  # fmt: skip
    return {
        "appl_id": appl, "core": core, "project_num": f"{kind}{core}-0{fy - 2020}",
        "fiscal_year": fy, "subproject_id": sub, "title": f"Grant {core}",
        "activity": activity, "institute": institute, "mechanism": "Non-SBIR/STTR",
        "total": total, "direct": None if total is None else total * 2 // 3,
        "indirect": None if total is None else total // 3, "project_start": date(2021, 7, 1),
        "project_end": end, "organization": "OURS" if ours else "ELSEWHERE", "ours": ours,
    }  # fmt: skip


def funding_inputs(members: list[dict], links: list[tuple[str, str]], awards: list[dict]) -> Inputs:
    return Inputs(
        {
            "members": pa.Table.from_pylist(members, schema=MEMBERS_SCHEMA),
            "grant_links": pa.Table.from_pylist(
                [{"person_id": p, "core": c, "assertion_id": f"asr_{p}_{c}"} for p, c in links],
                schema=SCHEMAS["grant_links"],
            ),
            "awards": pa.Table.from_pylist(awards, schema=SCHEMAS["awards"]),
        },
        [{"source": "reporter", "label": "RePORTER", "last_success_at": "2026-11-07T03:00:00Z"}],
        datetime(2026, 11, 7, tzinfo=UTC),
    )


def funding_figure(data: Inputs, metric: str, cohort: str = "group", **options: str) -> dict:
    m = metrics.get(metric)
    return compute(
        m, data, {"label": "Lab", "kind": cohort, "size": 2}, Period(2022, 2026, "fiscal"),
        m.resolve_options(options), small_number=5, today=TODAY,
    ).to_json(records=True)  # fmt: skip


TEAM = [{"person_id": "a", "name": "Ada", "start_year": None, "end_year": None},
        {"person_id": "b", "name": "Ben", "start_year": 2025, "end_year": None}]  # fmt: skip


def test_money_is_counted_once_per_record_without_subprojects() -> None:
    data = funding_inputs(
        TEAM,
        [("a", "P01CA1"), ("b", "P01CA1"), ("b", "R01GM2"), ("a", "R21NS3")],
        [
            award(1, "P01CA1", 2024, 1_000_000, kind="1", activity="P01"),
            award(2, "P01CA1", 2024, 300_000, sub="0001", activity="P01"),  # within the parent
            award(3, "P01CA1", 2025, 1_050_000, activity="P01"),
            award(4, "P01CA1", 2025, 80_000, kind="3", activity="P01"),  # a supplement
            award(5, "R01GM2", 2024, 500_000, institute="NIGMS"),  # before Ben joined
            award(6, "R01GM2", 2025, 510_000, institute="NIGMS"),
            award(7, "R21NS3", 2025, 200_000, institute="NINDS", activity="R21", ours=False),
        ],
    )  # fmt: skip
    out = funding_figure(data, "funding.awarded")
    rows = {r["fiscal_year"]: r for r in out["rows"]}
    assert rows[2024]["amount"] == 1_000_000 and rows[2024]["new"] == 1_000_000
    assert rows[2025]["amount"] == 1_050_000 + 80_000 + 510_000
    assert rows[2025]["supplement"] == 80_000 and rows[2025]["grants"] == 2
    assert out["totals"]["grants"] == 2 and out["totals"]["records"] == 4
    record = next(r for r in out["records"] if r["appl_id"] == 3)
    assert record["members"] == ["Ada", "Ben"] and record["type"] == "Continuing years"
    anywhere = funding_figure(data, "funding.awarded", awardee="anywhere")
    assert anywhere["totals"]["amount"] == out["totals"]["amount"] + 200_000
    direct = funding_figure(data, "funding.awarded", amount="direct")
    assert direct["totals"]["amount"] == sum(
        r["direct"] for r in out["records"]
    ) and "direct costs" in direct["notes"][0]  # fmt: skip
    by_institute = funding_figure(data, "funding.by_institute")
    assert sum(r["amount"] for r in by_institute["rows"]) == out["totals"]["amount"]
    assert [r["label"] for r in by_institute["rows"]] == ["NCI", "NIGMS"]
    assert by_institute["rows"][0]["detail"] == "National Cancer Institute"


def test_the_organization_counts_every_award_to_it() -> None:
    data = funding_inputs(
        TEAM[:1],
        [("a", "R01CA9")],
        [award(1, "R01CA9", 2025, 400_000), award(2, "U01AI8", 2025, 900_000),
         award(3, "R21CA7", 2025, 150_000, ours=False)],
    )  # fmt: skip
    out = funding_figure(data, "funding.awarded", cohort="organization")
    assert out["totals"]["amount"] == 1_300_000  # its own awards, with or without a member
    missing = funding_inputs(TEAM[:1], [("a", "R01CA9")], [award(1, "R01CA9", 2025, None)])
    noted = funding_figure(missing, "funding.awarded")
    assert noted["totals"]["grants"] == 1 and "do not state their total cost" in noted["notes"][1]


def test_grants_ending_soon_are_end_dates_not_gaps() -> None:
    data = funding_inputs(
        TEAM[:1],
        [("a", "R01CA1"), ("a", "R01CA2"), ("a", "R01CA3")],
        [award(1, "R01CA1", 2025, 1, end=date(2027, 3, 31)),
         award(2, "R01CA2", 2025, 1, end=date(2028, 6, 30)),
         award(3, "R01CA3", 2024, 1, end=date(2026, 6, 30))],
    )  # fmt: skip
    out = funding_figure(data, "funding.ending", months="12")
    assert [r["core_project_num"] for r in out["rows"]] == ["R01CA1"]
    assert out["period"]["label"] == "The next 12 months" and "not a gap" in out["notes"][0]
    assert len(funding_figure(data, "funding.ending", months="24")["rows"]) == 2


def test_the_demos_funding_figures_agree_to_the_dollar(demo) -> None:  # type: ignore[no-untyped-def]
    config, client = demo
    lake = Lake(config.data.home / "lake")
    try:
        subprojects = {
            r["appl_id"]
            for r in lake.rows(
                "SELECT appl_id FROM reporter_project WHERE subproject_id IS NOT NULL"
            )
        }
    finally:
        lake.close()
    assert len(subprojects) >= 6
    awarded = figure(client, "funding.awarded", start=2021, end=2026, records="true")
    total = awarded["totals"]["amount"]
    assert total > 10_000_000 and awarded["metric"]["ref"] == "funding.awarded/v1"
    assert sum(r["amount"] for r in awarded["rows"]) == total
    for metric in ("funding.by_institute", "funding.by_activity", "funding.by_mechanism"):
        out = figure(client, metric, start=2021, end=2026)
        assert sum(r["amount"] for r in out["rows"]) == total, metric
    activities = {r["label"] for r in figure(client, "funding.by_activity", start=2021,
                                               end=2026)["rows"]}  # fmt: skip
    assert {"P01", "P30", "T32", "K99", "R01"} <= activities
    assert not subprojects & {r["appl_id"] for r in awarded["records"]}
    assert any(r["type"] == "Supplements" for r in awarded["records"])
    groups_ = figure(client, "funding.by_group", start=2021, end=2026, kind="division")
    assert [r["group"] for r in groups_["rows"]] == sorted(
        (r["group"] for r in groups_["rows"]), key=str.lower)  # fmt: skip
    assert any("overlap" in n for n in groups_["notes"])
