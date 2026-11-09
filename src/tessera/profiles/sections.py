"""The sections of a profile that a researcher keeps, their fields, and the rules for each.

Every entry is a dictionary of a section's fields. Dates are a year, a year and month, or (for
talks) a full date, written ``2024``, ``2024-03``, or ``2024-03-18``. Text is trimmed; long text
keeps its paragraphs. Anything a section does not define is refused, so the stored entries
always mean the same thing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from tessera.core.errors import TesseraError

DATE = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$")
URL = re.compile(r"^https?://[^\s/]+\.[^\s]+$")


class ProfileError(TesseraError):
    """A profile entry is not as its section requires, or the change is not allowed."""


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str = "text"  # text, long, date, url, choice, pmid
    required: bool = False
    limit: int = 300
    choices: tuple[tuple[str, str], ...] = ()
    hint: str | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"key": self.key, "label": self.label, "kind": self.kind,
                               "required": self.required, "limit": self.limit}  # fmt: skip
        if self.choices:
            out["choices"] = [{"value": v, "label": label} for v, label in self.choices]
        if self.hint:
            out["hint"] = self.hint
        return out


@dataclass(frozen=True)
class Section:
    id: str
    label: str
    noun: str  # one entry, for buttons and the history: "a position"
    fields: tuple[Field, ...]
    single: bool = False  # at most one entry (the overview, the interests)
    most: int = 100
    newest_first: bool = True  # new entries go first; otherwise last

    @property
    def keys(self) -> set[str]:
        return {f.key for f in self.fields}

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "noun": self.noun, "single": self.single,
                "most": self.most, "fields": [f.to_json() for f in self.fields]}  # fmt: skip


START = Field("start", "From", "date", hint="2019 or 2019-09")
END = Field("end", "To", "date", hint="Leave empty if current")
NOTE = Field("description", "Description", "long", limit=1000)

SECTIONS: dict[str, Section] = {s.id: s for s in (
    Section("overview", "Overview", "an overview", (
        Field("text", "Overview", "long", required=True, limit=5000),), single=True),
    Section("interests", "Research interests", "research interests", (
        Field("text", "Research interests", "long", required=True, limit=2000),), single=True),
    Section("position", "Positions and appointments", "a position", (
        Field("title", "Title", required=True, limit=200),
        Field("organization", "Organization", required=True, limit=300),
        Field("department", "Department", limit=300),
        Field("location", "Location", limit=200),
        START, END, NOTE)),
    Section("education", "Education and training", "education or training", (
        Field("institution", "Institution", required=True, limit=300),
        Field("degree", "Degree or training", required=True, limit=120,
              hint="PhD, MD, BS, Postdoctoral fellowship, Residency"),
        Field("field", "Field of study", limit=200),
        Field("location", "Location", limit=200),
        START, Field("end", "Completed", "date", hint="2016 or 2016-05"))),
    Section("honor", "Honors and awards", "an honor", (
        Field("title", "Honor or award", required=True, limit=300),
        Field("organization", "Awarded by", limit=300),
        Field("year", "Year", "date", required=True, hint="2023"),
        NOTE)),
    Section("membership", "Memberships and service", "a membership or service role", (
        Field("role", "Role", required=True, limit=200, hint="Member, Reviewer, Committee chair"),
        Field("organization", "Organization or body", required=True, limit=300),
        START, END, NOTE)),
    Section("teaching", "Teaching and mentoring", "a course or mentee", (
        Field("title", "Course or person mentored", required=True, limit=300),
        Field("kind", "Kind", "choice", choices=(("course", "Course"), ("mentee", "Mentee"),
                                                 ("other", "Other"))),
        Field("role", "Role", limit=200, hint="Instructor, Course director, Primary mentor"),
        Field("organization", "Organization or program", limit=300),
        START, END, NOTE)),
    Section("talk", "Talks and presentations", "a talk", (
        Field("title", "Title", required=True, limit=400),
        Field("event", "Event", required=True, limit=300),
        Field("kind", "Kind", "choice", choices=(("invited", "Invited"), ("keynote", "Keynote"),
                                                 ("contributed", "Contributed"),
                                                 ("poster", "Poster"), ("other", "Other"))),
        Field("location", "Location", limit=200),
        Field("date", "Date", "date", required=True, hint="2025-04-12, 2025-04, or 2025"),
        Field("url", "Link", "url", limit=1000))),
    Section("link", "Links", "a link", (
        Field("label", "Label", required=True, limit=120, hint="Lab website, Google Scholar"),
        Field("url", "Address", "url", required=True, limit=1000)), most=20, newest_first=False),
    Section("featured", "Featured publications", "a featured publication", (
        Field("pmid", "PubMed ID", "pmid", required=True, hint="One of their confirmed papers"),
        Field("note", "Why it matters", limit=300)), most=10, newest_first=False),
)}  # fmt: skip


def _date(value: str, label: str, *, full: bool) -> str:
    match = DATE.match(value)
    if not match:
        raise ProfileError(f"{label}: {value!r} is not a year, a year and month, or a date.")
    year, month, day = (int(x) if x else None for x in match.groups())
    if not 1900 <= year <= 2100:  # type: ignore[operator]
        raise ProfileError(f"{label}: {year} is not a year between 1900 and 2100.")
    if month is not None and not 1 <= month <= 12:
        raise ProfileError(f"{label}: {month} is not a month.")
    if day is not None and (not full or not 1 <= day <= 31):
        raise ProfileError(f"{label}: give a year, or a year and month.")
    return value


def clean(section_id: str, data: Any) -> dict[str, Any]:
    """The entry's fields, checked and tidied; raises ProfileError naming the first problem."""
    section = SECTIONS.get(section_id)
    if section is None:
        raise ProfileError(f"There is no profile section {section_id!r}.")
    if not isinstance(data, dict):
        raise ProfileError("An entry's fields must be an object.")
    unknown = set(data) - section.keys
    if unknown:
        raise ProfileError(f"{section.label} entries have no {sorted(unknown)[0]!r}.")
    out: dict[str, Any] = {}
    for f in section.fields:
        raw = data.get(f.key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            if f.required:
                raise ProfileError(f"{section.label}: {f.label} is required.")
            continue
        if not isinstance(raw, (str, int)):
            raise ProfileError(f"{section.label}: {f.label} must be text.")
        text = str(raw).strip()
        if f.kind == "long":
            paragraphs = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
            text = "\n\n".join(re.sub(r"[ \t]+", " ", p).strip() for p in paragraphs if p.strip())
        else:
            text = re.sub(r"\s+", " ", text)
        if len(text) > f.limit:
            raise ProfileError(f"{section.label}: {f.label} is at most {f.limit:,} characters.")
        if f.kind == "date":
            text = _date(text, f.label, full=section_id == "talk")
        elif f.kind == "url" and not URL.match(text):
            raise ProfileError(f"{section.label}: {f.label} must be a web address (https://…).")
        elif f.kind == "choice" and text not in dict(f.choices):
            allowed = ", ".join(v for v, _ in f.choices)
            raise ProfileError(f"{section.label}: {f.label} is one of {allowed}.")
        elif f.kind == "pmid":
            text = re.sub(r"^(pmid:?|pub:)\s*", "", text, flags=re.I)
            if not text.isdigit():
                raise ProfileError(f"{section.label}: {f.label} is a PubMed ID, digits only.")
        out[f.key] = text
    start, end = out.get("start"), out.get("end")
    if start and end and end[:7] < start[:7]:
        raise ProfileError(f"{section.label}: it cannot end before it starts.")
    return out


__all__ = ["SECTIONS", "Field", "ProfileError", "Section", "clean"]
