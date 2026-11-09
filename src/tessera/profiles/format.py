"""How a profile entry reads, for CVs and the public site: its title, a line of detail, and its
dates ("Sep 2019 to present", with an en dash)."""

from __future__ import annotations

from typing import Any

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
ONGOING = {"position", "membership", "teaching"}  # no end date means "present"
DASH = "\u2013"


def when(value: str | None) -> str:
    """ "2019", "Sep 2019", or "12 Sep 2019"."""
    if not value:
        return ""
    year, _, rest = value.partition("-")
    if not rest:
        return year
    month, _, day = rest.partition("-")
    return f"{f'{int(day)} ' if day else ''}{MONTHS[int(month) - 1]} {year}"


def span(fields: dict[str, Any], section: str) -> str:
    start = when(fields.get("start"))
    end = when(fields.get("end")) or ("present" if section in ONGOING else "")
    if not start:
        return end
    return f"{start} {DASH} {end}" if end else f"{start} {DASH}"


def _join(*parts: Any) -> str:
    return " · ".join(str(p) for p in parts if p)


def lines(section: str, f: dict[str, Any]) -> tuple[str, str, str]:
    """(title, detail, dates) for an entry of a dated section."""
    if section == "position":
        return (
            f.get("title", ""),
            _join(f.get("department"), f.get("organization"), f.get("location")),
            span(f, section),
        )
    if section == "education":
        return (
            _join(f.get("degree"), f.get("field")),
            _join(f.get("institution"), f.get("location")),
            span(f, section),
        )
    if section == "honor":
        return f.get("title", ""), f.get("organization") or "", when(f.get("year"))
    if section == "membership":
        return f.get("role", ""), f.get("organization") or "", span(f, section)
    if section == "teaching":
        return f.get("title", ""), _join(f.get("role"), f.get("organization")), span(f, section)
    if section == "talk":
        return f.get("title", ""), _join(f.get("event"), f.get("location")), when(f.get("date"))
    return f.get("title") or f.get("label") or "", "", ""


__all__ = ["lines", "span", "when"]
