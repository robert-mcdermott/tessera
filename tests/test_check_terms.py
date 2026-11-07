from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_terms.py"
_spec = importlib.util.spec_from_file_location("check_terms", SCRIPT)
assert _spec and _spec.loader
check_terms = importlib.util.module_from_spec(_spec)
sys.modules["check_terms"] = check_terms
_spec.loader.exec_module(check_terms)


def hits(text: str, *terms: str) -> list[str]:
    parsed = check_terms.parse_terms(terms)
    return [h.term for h in check_terms.find_hits("x", text, parsed)]


def test_case_insensitive_whole_words() -> None:
    assert hits("Acme Corp makes things", "acme corp") == ["acme corp"]
    assert hits("ACME   CORP", "acme corp") == ["acme corp"]
    assert hits("acmecorporation", "acme corp") == []
    assert hits("the acme-corp site", "acme") == ["acme"]


def test_case_sensitive_terms() -> None:
    assert hits("reached the zenith of design", "=ZENITH") == []
    assert hits("compared with ZENITH", "=ZENITH") == ["ZENITH"]


def test_comments_and_blank_lines_ignored() -> None:
    assert check_terms.parse_terms(["# note", "", "  ", "=", "real"])[0].text == "real"
    assert len(check_terms.parse_terms(["# note", "", "  ", "=", "real"])) == 1


def test_line_numbers() -> None:
    parsed = check_terms.parse_terms(["secret"])
    found = list(check_terms.find_hits("f.md", "ok\nsecret here\nok", parsed))
    assert [(h.where, h.line) for h in found] == [("f.md", 2)]


def test_message_mode_skips_git_comments(tmp_path: Path) -> None:
    message = tmp_path / "MSG"
    message.write_text("Subject\n\n# secret in a git comment\nbody\n")
    assert check_terms.scan_message(message, check_terms.parse_terms(["secret"])) == []
    message.write_text("Subject\n\nsecret in the body\n")
    assert len(check_terms.scan_message(message, check_terms.parse_terms(["secret"]))) == 1


def test_missing_term_list_is_not_an_error(tmp_path: Path) -> None:
    assert check_terms.main(["--terms", str(tmp_path / "none.txt")]) == 0
