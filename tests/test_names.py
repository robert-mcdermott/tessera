from __future__ import annotations

import pytest

from tessera.config import OrganizationConfig
from tessera.evidence.names import (
    Match,
    best_match,
    compare,
    family_keys,
    fold,
    initials_of,
    key,
    make_name,
    parse_display_name,
)
from tessera.evidence.orgmatch import OrgMatcher, emails_in


@pytest.mark.parametrize(
    ("text", "family", "given", "initials"),
    [
        ("Ada B. Okafor, MD, PhD", "Okafor", "Ada B.", "AB"),
        ("Okafor, Ada B", "Okafor", "Ada B", "AB"),
        ("Dr. Jean-Luc Picard", "Picard", "Jean-Luc", "JL"),
        ("Ana de la Cruz", "de la Cruz", "Ana", "A"),
        ("Prof. Ludwig van Beethoven Jr", "van Beethoven", "Ludwig", "L"),
        ("Okafor", "Okafor", "", ""),
    ],
)
def test_parse_display_name(text: str, family: str, given: str, initials: str) -> None:
    name = parse_display_name(text)
    assert name is not None
    assert (name.family, name.given, name.initials) == (family, given, initials)


def test_parse_rejects_empty() -> None:
    assert parse_display_name("") is None
    assert parse_display_name("  MD ") is None
    assert make_name("", "Ada") is None


def test_folding_and_keys() -> None:
    assert fold("Søren Ölz-Łukasz I\u0131\u0130") == "soren olz-lukasz iii"
    assert key("van der Berg") == "vanderberg"
    assert family_keys("van der Berg") == {"vanderberg", "berg"}
    assert family_keys("Smith-Jones") == {"smithjones", "smith", "jones"}
    assert initials_of("Ada Beth") == "AB"
    assert initials_of("ADA") == "A"  # an all-caps first name, not initials


A = make_name("Okafor", "Ada B")


@pytest.mark.parametrize(
    ("other", "expected"),
    [
        (make_name("Okafor", "Ada"), Match.FULL),
        (make_name("OKAFOR", "ADA B"), Match.FULL),
        (make_name("Okafor", "A"), Match.INITIALS),
        (make_name("Okafor", "AB", "AB"), Match.INITIALS),  # PubMed ForeName holding initials
        (make_name("Okafor", "A B", "AB"), Match.INITIALS),
        (make_name("Okafor", "Ada C"), Match.MIDDLE_CONFLICT),
        (make_name("Okafor", "AC", "AC"), Match.MIDDLE_CONFLICT),
        (make_name("Okafor", "Anna"), Match.GIVEN_CONFLICT),
        (make_name("Okafor", "Bao"), Match.NONE),
        (make_name("Okafur", "Ada"), Match.NONE),
        (make_name("Okafor"), Match.INITIALS),
    ],
)
def test_compare(other, expected) -> None:  # type: ignore[no-untyped-def]
    assert compare(A, other) is expected


def test_best_match_uses_any_variant() -> None:
    variants = [make_name("Okafor", "Ada"), make_name("Okafor-Mensah", "Ada B")]
    assert best_match(variants, make_name("Mensah", "Ada B")) is Match.FULL
    assert best_match(variants, make_name("Okafor", "Anna")) is Match.GIVEN_CONFLICT


ORG = OrganizationConfig(
    name="Example University",
    aliases=("Example University", "Example Univ"),
    affiliation_patterns=("example univ*", "exu medical center"),
    email_domains=("example.edu",),
    exclusions=("Example University of Technology",),
)


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Dept of Medicine, Example University, Example City", "alias"),
        ("EXAMPLE UNIV. School of Medicine", "alias"),
        ("Exu Medical Center, Example City", "pattern"),
        ("Example Universität", "pattern"),
        ("Lab of Things. ada@med.example.edu.", "email"),
        ("Example University of Technology and Example University Hospital", "alias"),
        ("Example University of Technology, Techville", None),
        ("Northexample University", None),
        ("Examples Universal Inc", None),
        ("someone@notexample.edu", None),
        ("", None),
    ],
)
def test_org_matcher(text: str, kind: str | None) -> None:
    found = OrgMatcher(ORG).match(text)
    assert (found.kind if found else None) == kind


def test_emails_in() -> None:
    assert emails_in("Example University. Ada.Okafor@Example.edu.") == ["ada.okafor@example.edu"]
    assert emails_in(None) == []
