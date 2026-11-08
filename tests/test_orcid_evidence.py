"""ORCID evidence in authorship scoring (authorship/v1.1)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from tessera.config import load_config
from tessera.evidence.authorship import (
    WEIGHTS,
    Candidate,
    Paper,
    Profile,
    Seeds,
    Slot,
    _consistency,
    score_candidate,
)
from tessera.evidence.names import Match, make_name
from tessera.evidence.orgmatch import OrgMatcher
from tessera.evidence.store import PersonRecord

MINE = "0000-0003-9000-0014"
OLD = "0000-0003-9000-0022"  # deprecated in favor of MINE
OTHER = "0000-0003-9000-0030"


@pytest.fixture
def matcher(config_file: Path) -> OrgMatcher:
    return OrgMatcher(load_config(config_file, env={}).organization)


def candidate(
    *,
    orcids: tuple[str, ...] = (),
    rejected: tuple[str, ...] = (),
    slot_orcid: str | None = None,
    printed: str | None = None,
) -> Candidate:
    name = make_name("Example", "Avery", None)
    assert name is not None
    person = PersonRecord(
        "per_1", "Avery Example", "active", [name], {"orcid": set(orcids)}, {"orcid": set(rejected)}
    )
    slot = Slot("100", 1, name, slot_orcid, [], 3, printed or slot_orcid)
    return Candidate(Profile(person, {"example"}), slot, Match.FULL)


def score(cand: Candidate, matcher: OrgMatcher, paper: Paper | None = None) -> dict[str, dict]:
    score_candidate(cand, paper or Paper("100"), matcher, (Counter(), Counter()), None)
    return {f["feature"]: f for f in cand.features}


def test_a_deprecated_id_counts_as_the_one_that_replaced_it(matcher: OrgMatcher) -> None:
    found = score(candidate(orcids=(MINE,), slot_orcid=MINE, printed=OLD), matcher)
    assert "orcid_match" in found and "orcid_conflict" not in found
    assert f"which replaced {OLD}" in found["orcid_match"]["explanation"]


def test_a_rejected_id_counts_against(matcher: OrgMatcher) -> None:
    found = score(candidate(rejected=(OTHER,), slot_orcid=OTHER), matcher)
    assert found["orcid_rejected"]["weight"] == WEIGHTS["orcid_rejected"]
    assert "orcid_conflict" not in found
    found = score(candidate(orcids=(MINE,), slot_orcid=OTHER), matcher)
    assert "orcid_conflict" in found


def test_a_second_id_suggested_for_the_person_is_not_a_conflict(matcher: OrgMatcher) -> None:
    cand = candidate(orcids=(MINE,), slot_orcid=OTHER)
    cand.profile.person.suggested["orcid"] = {OTHER}
    found = score(cand, matcher)
    assert "orcid_conflict" not in found and "orcid_match" not in found


def test_works_on_the_persons_orcid_record(matcher: OrgMatcher) -> None:
    cand = candidate(orcids=(MINE,))
    cand.profile.orcid_pmids["100"] = (MINE, "Europe PMC")
    found = score(cand, matcher)
    assert found["orcid_work"]["weight"] == WEIGHTS["orcid_work"]
    assert "added by Europe PMC" in found["orcid_work"]["explanation"]

    by_doi = candidate(orcids=(MINE,))
    by_doi.profile.orcid_dois["10.1000/x"] = (MINE, "ORCID")
    assert "orcid_work" in score(by_doi, matcher, Paper("100", doi="10.1000/x"))
    assert "orcid_work" not in score(candidate(orcids=(MINE,)), matcher)


def seeds(slot_orcids: dict[str, str]) -> Seeds:
    return Seeds(orcids=Counter(slot_orcids.values()), slot_orcids=dict(slot_orcids))


def run_consistency(orcid: str, s: Seeds, pmid: str = "100") -> dict[str, dict]:
    f: list[dict] = []
    _consistency(f, orcid, pmid, s, "pubmed:100#author1")
    return {item["feature"]: item for item in f}


def test_the_same_id_on_other_papers_is_evidence() -> None:
    assert "orcid_consistent" in run_consistency(MINE, seeds({"200": MINE, "300": MINE}))
    # Leave-one-out: the paper's own iD does not count for itself.
    own = run_consistency(MINE, seeds({"100": MINE, "200": MINE}))
    assert own["orcid_consistent_one"]["value"] == 1


def test_a_different_id_counts_against_only_when_one_id_clearly_predominates() -> None:
    clear = seeds({"200": MINE, "300": MINE, "400": MINE, "500": MINE})
    against = run_consistency(OTHER, clear)
    assert against["orcid_inconsistent"]["weight"] == WEIGHTS["orcid_inconsistent"]
    assert "some people have two iDs" in against["orcid_inconsistent"]["explanation"]
    # Too few papers, or two iDs in use: no judgment.
    assert run_consistency(OTHER, seeds({"200": MINE, "300": MINE})) == {}
    mixed = seeds({"200": MINE, "300": MINE, "400": MINE, "500": OLD, "600": OLD})
    assert run_consistency(OTHER, mixed) == {}


# --- on the synthetic organization ------------------------------------------------------------


def identifiers(engine, person_id: str) -> dict[str, tuple[str, str]]:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    from tessera.ledger.tables import person_identifier

    with engine.connect() as conn:
        rows = conn.execute(
            select(person_identifier).where(
                person_identifier.c.person_id == person_id, person_identifier.c.scheme == "orcid"
            )
        ).mappings()
        return {r["value"]: (r["status"], r["source"]) for r in rows}


def set_identifier_status(engine, person_id: str, value: str, status: str) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import update

    from tessera.ledger.tables import person_identifier

    with engine.begin() as conn:
        conn.execute(
            update(person_identifier)
            .where(person_identifier.c.person_id == person_id, person_identifier.c.value == value)
            .values(status=status)
        )


def test_ids_on_a_persons_papers_are_suggested_not_accepted(instance, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from .test_evidence import make_scenario

    s = make_scenario(instance, tmp_path)
    proposed = 0
    for researcher in s.world.insiders:
        held = identifiers(s.engine, s.person_id(researcher.key))
        if researcher.profile_id is None:  # roster members bring their iD (accepted)
            if researcher.orcid:
                assert held == {researcher.orcid: ("accepted", "roster")}
            continue
        for value, (status, source) in held.items():
            assert (value, status, source) == (researcher.orcid, "suggested", "papers")
            proposed += 1
    assert proposed >= 5  # grant holders whose papers carry their iD
    s.engine.dispose()


def test_confirming_or_rejecting_a_suggested_id(instance, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from tessera.evidence.resolve import resolve

    from .test_evidence import TODAY, make_scenario

    s = make_scenario(instance, tmp_path)
    holders = [
        r for r in s.world.insiders
        if r.profile_id is not None
        and r.orcid in identifiers(s.engine, s.person_id(r.key))
    ]  # fmt: skip
    confirm, refuse = holders[0], holders[1]
    set_identifier_status(s.engine, s.person_id(confirm.key), confirm.orcid, "accepted")
    set_identifier_status(s.engine, s.person_id(refuse.key), refuse.orcid, "rejected")
    resolve(s.instance.config, s.instance.home, s.engine, today=TODAY)

    features = {key: {f["feature"] for f in row["evidence"]} for key, row in s.authored().items()}
    confirmed = [f for (who, _), f in features.items() if who == confirm.key]
    assert any("orcid_match" in f for f in confirmed)
    assert any("orcid_work" in f for f in confirmed)  # its public record is now used
    refused = [f for (who, _), f in features.items() if who == refuse.key]
    assert any("orcid_rejected" in f for f in refused)
    # A rejected iD is not proposed again.
    assert identifiers(s.engine, s.person_id(refuse.key)) == {refuse.orcid: ("rejected", "papers")}
    s.engine.dispose()


def test_resolving_again_changes_nothing(instance, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from tessera.evidence.resolve import resolve

    from .test_evidence import TODAY, make_scenario

    s = make_scenario(instance, tmp_path)
    again = resolve(s.instance.config, s.instance.home, s.engine, today=TODAY)
    authorship = again.counts["authorship"]
    assert (authorship["store"]["updated"], authorship["identifiers"]["proposed"]) == (0, 0)
    s.engine.dispose()
