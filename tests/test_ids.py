from __future__ import annotations

import time
import uuid

import pytest

from tessera.core import ids


def test_new_id_shape_and_roundtrip() -> None:
    value = ids.new_id("per")
    assert value.startswith("per_")
    assert len(value) == 4 + 26
    prefix, parsed = ids.parse_id(value)
    assert prefix == "per"
    assert parsed.version == 7
    assert parsed.variant == uuid.RFC_4122


def test_ids_are_strictly_increasing_within_a_process() -> None:
    values = [ids.new_id("asr") for _ in range(5000)]
    assert values == sorted(values)
    assert len(set(values)) == len(values)


def test_uuid7_embeds_current_time() -> None:
    before = time.time_ns() // 1_000_000
    value = ids.uuid7()
    after = time.time_ns() // 1_000_000
    stamp = value.int >> 80
    assert before <= stamp <= after + 1


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "per_",
        "xyz_0J8ZQ3W5T4K2M9B7XHRC1DGFNE",
        "per_0J8ZQ3W5T4K2M9B7XHRC1DGFN",
        "per_0J8ZQ3W5T4K2M9B7XHRC1DGFNI",
    ],
)
def test_parse_id_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        ids.parse_id(bad)
    assert not ids.is_id(bad)


def test_is_id_checks_prefix() -> None:
    value = ids.new_id("unt")
    assert ids.is_id(value)
    assert ids.is_id(value, "unt")
    assert not ids.is_id(value, "per")


def test_unknown_prefix_rejected() -> None:
    with pytest.raises(ValueError):
        ids.new_id("pub")
