"""Dated unit memberships from the roster (REC-08)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tessera.core.errors import TesseraError
from tessera.evidence import store
from tessera.evidence.memberships import has_ended, is_current, parse_partial_date
from tessera.evidence.people import import_roster
from tessera.ledger.db import make_engine

from .conftest import Instance

TODAY = date(2026, 11, 7)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2015", "2015"),
        ("2015-7", "2015-07"),
        ("2015-07-01", "2015-07-01"),
        ("7/2015", "2015-07"),
        ("7/1/2015", "2015-07-01"),
        ("", None),
    ],
)
def test_partial_dates(text: str, expected: str | None) -> None:
    assert parse_partial_date(text) == expected


@pytest.mark.parametrize("text", ["2015-13", "July 2015", "2015-02-30"])
def test_bad_dates(text: str) -> None:
    with pytest.raises(TesseraError):
        parse_partial_date(text)


def test_current_at_the_precision_given() -> None:
    assert not has_ended("2026", TODAY) and has_ended("2025", TODAY)
    assert not has_ended("2026-11", TODAY) and has_ended("2026-10", TODAY)
    assert not has_ended("2026-11-07", TODAY) and has_ended("2026-11-06", TODAY)
    assert is_current({}, TODAY) and is_current({"start": "2015"}, TODAY)
    assert not is_current({"start": "2027"}, TODAY)  # not started yet
    assert not is_current({"start": "2010", "end": "2018"}, TODAY)


def memberships(engine, person_id: str) -> dict[str, dict]:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    from tessera.ledger.tables import unit

    with engine.connect() as conn:
        units = {r.id: r.name for r in conn.execute(select(unit.c.id, unit.c.name))}
    return {
        units[r["object_id"]]: r["qualifiers"]
        for r in store.assertions_for(engine, subject_id=person_id, predicate="member_of")
    }


def test_roster_dates_concurrent_memberships_and_former_members(
    instance: Instance, tmp_path: Path
) -> None:
    engine = make_engine(instance.config.ledger_url)
    roster = tmp_path / "roster.csv"
    roster.write_text(
        "family,given,email,unit,title,start,end\n"
        "Okafor,Ada,ada@example.edu,Department of Medicine,Professor,2015,\n"
        "Okafor,Ada,ada@example.edu,Cancer Biology Program,Member,2019-07,\n"
        "Lindqvist,Bo,bo@example.edu,Department of Pathology,Fellow,2012,2018-06\n"
        "Moreau,Cy,cy@example.edu,Department of Neurology,,2020,2015\n"
        "Novak,Di,di@example.edu,Department of Neurology,,someday,\n",
        encoding="utf-8",
    )
    stats = import_roster(engine, roster, today=TODAY)
    assert (stats.created, stats.matched) == (2, 1)
    assert [s.split(":")[0] for s in stats.skipped] == ["line 5", "line 6"]
    people = {p.display_name: p for p in store.load_people(engine, ("active", "former"))}
    ada, bo = people["Ada Okafor"], people["Bo Lindqvist"]
    assert (ada.status, bo.status) == ("active", "former")  # every membership of Bo ended
    assert memberships(engine, ada.id) == {
        "Department of Medicine": {"title": "Professor", "start": "2015"},
        "Cancer Biology Program": {"title": "Member", "start": "2019-07"},
    }
    assert memberships(engine, bo.id)["Department of Pathology"]["end"] == "2018-06"

    # The roster is the source: importing it again with Bo back updates his dates.
    roster.write_text(
        "family,given,email,unit,title,start,end\n"
        "Lindqvist,Bo,bo@example.edu,Department of Pathology,Fellow,2012,\n",
        encoding="utf-8",
    )
    import_roster(engine, roster, today=TODAY)
    assert memberships(engine, bo.id)["Department of Pathology"] == {
        "title": "Fellow",
        "start": "2012",
    }
    assert {p.id: p.status for p in store.load_people(engine)}[bo.id] == "active"
    engine.dispose()


def test_profiles_and_search_show_current_units(instance: Instance, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from tessera.api.app import create_app
    from tessera.jobs.build import build_search

    engine = make_engine(instance.config.ledger_url)
    roster = tmp_path / "roster.csv"
    roster.write_text(
        "family,given,unit,start,end\n"
        "Okafor,Ada,Department of Medicine,2015,\n"
        "Okafor,Ada,Department of Pathology,2008,2014\n",
        encoding="utf-8",
    )
    import_roster(engine, roster)
    build_search(instance.config, instance.home, engine)
    engine.dispose()
    with TestClient(create_app(instance.config)) as client:
        [person] = client.get("/api/v1/people").json()["data"]
        assert person["units"] == ["Department of Medicine"]  # past units are not current
        profile = client.get(f"/api/v1/people/{person['id']}").json()["data"]
        assert [(u["unit"], u["current"]) for u in profile["units"]] == [
            ("Department of Medicine", True),
            ("Department of Pathology", False),
        ]
