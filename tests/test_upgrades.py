"""The upgrade check (PLT-18) finds what an upgrade loses or changes. The check itself, which
installs every earlier release, runs as ``scripts/check-upgrades.sh``; here, its comparison."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_upgrades.py"
spec = importlib.util.spec_from_file_location("check_upgrades", SCRIPT)
assert spec and spec.loader
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

BEFORE = {
    "person": {'["per_1"]': {"id": "per_1", "name": "Ada Lovelace", "status": "active"}},
    "unit": {
        '["unt_1"]': {"id": "unt_1", "name": "Division", "parent_id": None},
        '["unt_2"]': {"id": "unt_2", "name": "Lab", "parent_id": "unt_1"},
    },
    "assertion": {
        '["asr_1"]': {"id": "asr_1", "predicate": "member_of", "qualifiers": {},
                      "qualifier_key": ""},
        '["asr_2"]': {"id": "asr_2", "predicate": "authored",
                      "qualifiers": {"author_position": 2}, "qualifier_key": ""},
    },
    "setting": {'["operations.last_backup"]': {"key": "operations.last_backup", "value": {}}},
}  # fmt: skip


def upgraded() -> dict:
    after = copy.deepcopy(BEFORE)
    for row in after["unit"].values():
        del row["parent_id"]
    after["unit_parent"] = {json.dumps(["unt_2", "unt_1"]): {"child_id": "unt_2",
                                                             "parent_id": "unt_1"}}  # fmt: skip
    after["assertion"]['["asr_1"]'] |= {"qualifiers": {"role": "member", "source": "roster"},
                                        "qualifier_key": "member"}  # fmt: skip
    after["person"]['["per_1"]']["public"] = False  # a column a later release added
    after["setting"]['["operations.last_backup"]']["value"] = {"at": "later"}
    return after


def test_an_upgrade_that_keeps_everything_passes() -> None:
    assert check.compare_ledgers(BEFORE, upgraded(), "x") == []


def test_what_an_upgrade_loses_or_changes_is_found() -> None:
    gone = upgraded()
    del gone["person"]['["per_1"]']
    assert check.compare_ledgers(BEFORE, gone, "x") == ['x: person row ["per_1"] is gone']

    changed = upgraded()
    changed["person"]['["per_1"]']["name"] = "A. Lovelace"
    assert check.compare_ledgers(BEFORE, changed, "x") == [
        "x: person row [\"per_1\"] column name: 'Ada Lovelace' became 'A. Lovelace'"
    ]

    lost_parent = upgraded()
    lost_parent["unit_parent"] = {}
    assert check.compare_ledgers(BEFORE, lost_parent, "x") == [
        "x: unit unt_2's parent unt_1 is lost"
    ]

    other_qualifiers = upgraded()
    other_qualifiers["assertion"]['["asr_2"]']["qualifiers"] = {}
    assert len(check.compare_ledgers(BEFORE, other_qualifiers, "x")) == 1

    wrong_role = upgraded()
    wrong_role["assertion"]['["asr_1"]']["qualifier_key"] = "leader"
    assert len(check.compare_ledgers(BEFORE, wrong_role, "x")) == 1

    no_table = upgraded()
    del no_table["person"]
    assert check.compare_ledgers(BEFORE, no_table, "x") == ["x: table person is gone"]


def test_files_are_compared_by_their_contents(tmp_path: Path) -> None:
    (tmp_path / "pubmed").mkdir()
    (tmp_path / "pubmed" / "part-0.parquet").write_bytes(b"rows")
    before = check.digests(tmp_path)
    (tmp_path / "pubmed" / "part-0.parquet").write_bytes(b"other rows")
    assert check.digests(tmp_path) != before and set(check.digests(tmp_path)) == set(before)


def test_values_are_compared_as_data() -> None:
    assert check.plain('{"b": 1, "a": 2}') == {"a": 2, "b": 1}
    assert check.plain("[1, 2]") == [1, 2]
    assert check.plain("{not json") == "{not json"
