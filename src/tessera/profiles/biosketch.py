"""NIH biosketches, prepared for SciENcv (PRF-04).

For applications due on or after January 25, 2026, NIH's biosketch is the Biographical Sketch
Common Form with the NIH Biographical Sketch Supplement (NOT-OD-26-018), completed and certified
by the investigator in SciENcv. Tessera arranges the content in the form's sections, checks
NIH's limits as it is written, and gives each section as text ready to paste into SciENcv. It
does not produce the certified PDF; only SciENcv does.

A biosketch is a CV document of its own kind: its ``spec`` holds what the researcher writes (the
personal statement, contributions to science) and chooses (education, positions, honors,
products); the rest is built from the profile and confirmed records whenever it is shown.
Problems are reported, not refused, so a draft can be saved while it is still too long.
"""

from __future__ import annotations

import re
from typing import Any

from tessera.profiles import cv
from tessera.profiles.sections import ProfileError

FORM = "nih-2026-01"
FORM_LABEL = (
    "NIH Biographical Sketch Common Form and NIH Biographical Sketch Supplement, "
    "for applications due on or after January 25, 2026 (NOT-OD-26-018)"
)
LIMITS = {
    "statement": 3500,
    "contribution": 2000,
    "contributions": 5,
    "honors": 15,
    "products_related": 5,
    "products_other": 5,
}
# Never more than this, whatever NIH allows, so a draft stays a draft.
CAPS = {"text": 12000, "list": 200, "products": 20, "contributions": 10}
PRODUCT_TEXT = 1000

# What a narrative must not hold: NIH allows no citations in them.
CITATIONS = (
    (re.compile(r"\bPMID[:\s]*\d{5,9}\b", re.I), "a PubMed ID"),
    (re.compile(r"\bPMC\d{4,9}\b"), "a PubMed Central ID"),
    (re.compile(r"\b(?:doi:\s*)?10\.\d{4,9}/[^\s,;]+", re.I), "a DOI"),
    (re.compile(r"\bet al\.?(?:,?\s*\(?\d{4}\)?)?", re.I), "an “et al.” reference"),
    (re.compile(r"\[\s*\d+(?:\s*[,\u2013-]\s*\d+)*\s*\]"), "a numbered reference"),
)


def default_spec(available: dict[str, list[dict[str, Any]]], organization: str) -> dict[str, Any]:
    """A first biosketch: the person's current title, everything chosen, nothing written."""
    current = [p for p in available.get("position", []) if p.get("current")]
    # A title such as "Associate Professor" over a group role such as "Lead".
    title = next((p["fields"]["title"] for p in current if p.get("titled")), None) or next(
        (p["fields"].get("title") for p in current if p["fields"].get("title")), ""
    )
    return {
        "position_title": title,
        "organization": organization,
        "preparation": None,
        "positions": None,
        "products_related": [],
        "products_other": [],
        "statement": "",
        "honors": None,
        "contributions": [],
    }


def _text(value: Any, label: str, cap: int = CAPS["text"]) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ProfileError(f"{label} must be text.")
    text = value.replace("\r\n", "\n").strip()
    if len(text) > cap:
        raise ProfileError(f"{label} is far too long to keep ({len(text):,} characters).")
    return text


def _ids(value: Any, label: str) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > CAPS["list"]:
        raise ProfileError(f"{label}: a list of entries.")
    return list(dict.fromkeys(str(v) for v in value))


def _products(value: Any, label: str) -> list[dict[str, str]]:
    if not value:
        return []
    if not isinstance(value, list) or len(value) > CAPS["products"]:
        raise ProfileError(f"{label}: a list of products.")
    out = []
    for item in value:
        if isinstance(item, dict) and item.get("pmid"):
            pmid = re.sub(r"\D", "", str(item["pmid"]))
            if not pmid:
                raise ProfileError(f"{label}: a PubMed ID is digits.")
            out.append({"pmid": pmid})
        elif isinstance(item, dict) and item.get("text"):
            out.append({"text": _text(item["text"], label, PRODUCT_TEXT)})
        else:
            raise ProfileError(f"{label}: each product is a paper (pmid) or a citation (text).")
    return out


def clean_spec(spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise ProfileError("A biosketch's spec is an object.")
    contributions = spec.get("contributions") or []
    if not isinstance(contributions, list) or len(contributions) > CAPS["contributions"]:
        raise ProfileError("Contributions to science: a list.")
    return {
        "position_title": _text(spec.get("position_title"), "Position title", 300),
        "organization": _text(spec.get("organization"), "Organization", 300),
        "preparation": _ids(spec.get("preparation"), "Professional preparation"),
        "positions": _ids(spec.get("positions"), "Appointments and positions"),
        "products_related": _products(spec.get("products_related"), "Products closely related"),
        "products_other": _products(spec.get("products_other"), "Other significant products"),
        "statement": _text(spec.get("statement"), "Personal statement"),
        "honors": _ids(spec.get("honors"), "Honors"),
        "contributions": [
            _text(c.get("text") if isinstance(c, dict) else c, "A contribution to science")
            for c in contributions
        ],
    }


# --- building it ---------------------------------------------------------------------


def _nih_date(value: str | None) -> str:
    """NIH writes dates as MM/YYYY; a year alone stays a year."""
    if not value:
        return ""
    year, _, rest = value.partition("-")
    return f"{rest[:2]}/{year}" if rest else year


def _chosen(rows: list[dict[str, Any]], pick: list[str] | None) -> list[dict[str, Any]]:
    if pick is None:
        return rows
    by_id = {str(r["id"]): r for r in rows}
    return [by_id[i] for i in pick if i in by_id]


def _product(item: dict[str, str], papers: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if "text" in item:
        return {"text": item["text"], "authors": [], "rest": item["text"], "manual": True}
    paper = papers.get(item["pmid"])
    if paper is None:
        return None
    names, rest = cv.citation(paper)
    authors = [{"name": n, "self": s} for n, s in names]
    plain = ", ".join(a["name"] for a in authors)
    return {
        "pmid": item["pmid"],
        "authors": authors,
        "rest": rest,
        "text": f"{plain}. {rest}" if plain else rest,
    }


def build(
    spec: dict[str, Any], available: dict[str, list[dict[str, Any]]], header: dict[str, Any]
) -> list[dict[str, Any]]:
    """The biosketch in NIH's order: the Common Form's sections, then the Supplement's."""
    papers = {str(p["id"]): p for p in available.get("publications", [])}
    preparation = [
        r["fields"] for r in _chosen(available.get("education", []), spec["preparation"])
    ]
    positions = [r["fields"] for r in _chosen(available.get("position", []), spec["positions"])]
    honors = [r["fields"] for r in _chosen(available.get("honor", []), spec["honors"])]
    related = [p for p in (_product(i, papers) for i in spec["products_related"]) if p]
    other = [p for p in (_product(i, papers) for i in spec["products_other"]) if p]
    return [
        {
            "id": "identity",
            "form": "common",
            "title": "Identifying information",
            "rows": [
                {"label": "Name", "value": header["name"]},
                {"label": "ORCID iD", "value": header.get("orcid") or ""},
                {"label": "Position title", "value": spec["position_title"]},
                {"label": "Organization and location", "value": spec["organization"]},
            ],
        },
        {
            "id": "preparation",
            "form": "common",
            "title": "Professional preparation",
            "rows": [
                {
                    "institution": f.get("institution", ""),
                    "location": f.get("location", ""),
                    "degree": f.get("degree", ""),
                    "start": _nih_date(f.get("start")),
                    "end": _nih_date(f.get("end")),
                    "field": f.get("field", ""),
                }
                for f in preparation
            ],
        },
        {
            "id": "positions",
            "form": "common",
            "title": "Appointments and positions",
            "rows": [
                {
                    "start": _nih_date(f.get("start")),
                    "end": _nih_date(f.get("end")) or "Present",
                    "title": f.get("title", ""),
                    "organization": f.get("organization", ""),
                    "location": f.get("location", ""),
                }
                for f in positions
            ],
        },
        {
            "id": "products",
            "form": "common",
            "title": "Products",
            "related": related,
            "other": other,
        },
        {
            "id": "certification",
            "form": "common",
            "title": "Certification",
            "text": "Completed and certified by the investigator in SciENcv.",
        },
        {
            "id": "statement",
            "form": "supplement",
            "title": "Personal statement",
            "text": spec["statement"],
        },
        {
            "id": "honors",
            "form": "supplement",
            "title": "Honors",
            "rows": [
                {
                    "year": f.get("year", ""),
                    "title": f.get("title", ""),
                    "organization": f.get("organization", ""),
                }
                for f in honors
            ],
        },
        {
            "id": "contributions",
            "form": "supplement",
            "title": "Contributions to science",
            "items": [c for c in spec["contributions"] if c],
        },
    ]


def citations_in(text: str) -> list[str]:
    """What looks like a citation in a narrative, by kind."""
    return [label for pattern, label in CITATIONS if pattern.search(text or "")]


def checks(
    spec: dict[str, Any], built: list[dict[str, Any]], available: dict[str, list[dict[str, Any]]]
) -> list[dict[str, str]]:
    """What NIH would not accept yet, each with where it is and what to do."""
    out: list[dict[str, str]] = []
    part = {s["id"]: s for s in built}

    def add(where: str, message: str) -> None:
        out.append({"part": where, "message": message})

    if not part["identity"]["rows"][1]["value"]:
        add(
            "identity",
            "The Common Form requires an ORCID iD. Confirm one on the profile "
            "(Identifiers), and link it to eRA Commons.",
        )
    if not spec["position_title"]:
        add("identity", "Give a position title.")
    if not part["preparation"]["rows"]:
        add("preparation", "No education or training is included; add it to the profile.")
    for kind, label in (
        ("related", "Products closely related to the project"),
        ("other", "Other significant products"),
    ):
        count = len(part["products"][kind])
        if count > LIMITS[f"products_{kind}"]:
            add("products", f"{label}: {count}, {count - 5} more than NIH's 5.")
    confirmed = {str(p["id"]) for p in available.get("publications", [])}
    for item in spec["products_related"] + spec["products_other"]:
        if "pmid" in item and item["pmid"] not in confirmed:
            add("products", f"PubMed {item['pmid']} is not one of their confirmed papers.")
    length = len(spec["statement"])
    if not length:
        add("statement", "Write the personal statement.")
    elif length > LIMITS["statement"]:
        add(
            "statement",
            f"Personal statement: {length:,} characters, "
            f"{length - LIMITS['statement']:,} more than NIH's 3,500.",
        )
    for found in citations_in(spec["statement"]):
        add("statement", f"The personal statement holds {found}; NIH allows no citations in it.")
    count = len(part["honors"]["rows"])
    if count > LIMITS["honors"]:
        add("honors", f"Honors: {count}, {count - LIMITS['honors']} more than NIH's 15.")
    contributions = part["contributions"]["items"]
    if len(contributions) > LIMITS["contributions"]:
        add(
            "contributions",
            f"Contributions to science: {len(contributions)}, "
            f"{len(contributions) - 5} more than NIH's 5.",
        )
    for n, text in enumerate(contributions, start=1):
        if len(text) > LIMITS["contribution"]:
            add(
                "contributions",
                f"Contribution {n}: {len(text):,} characters, "
                f"{len(text) - LIMITS['contribution']:,} more than NIH's 2,000.",
            )
        for found in citations_in(text):
            add("contributions", f"Contribution {n} holds {found}; NIH allows no citations in it.")
    return out


# --- for SciENcv and for keeping -------------------------------------------------------


def _range(row: dict[str, str]) -> str:
    return " - ".join(x for x in (row.get("start"), row.get("end")) if x)


def sciencv(built: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Each section as plain text, in the order SciENcv asks for it, ready to paste."""
    out = []
    for s in built:
        if s["id"] == "certification":
            continue
        if s["id"] == "identity":
            text = "\n".join(f"{r['label']}: {r['value']}" for r in s["rows"] if r["value"])
        elif s["id"] == "preparation":
            text = "\n".join(
                " | ".join(
                    x
                    for x in (
                        ", ".join(y for y in (r["institution"], r["location"]) if y),
                        r["degree"],
                        _range(r),
                        r["field"],
                    )
                    if x
                )
                for r in s["rows"]
            )
        elif s["id"] == "positions":
            text = "\n".join(
                " | ".join(
                    x
                    for x in (
                        _range(r),
                        r["title"],
                        ", ".join(y for y in (r["organization"], r["location"]) if y),
                    )
                    if x
                )
                for r in s["rows"]
            )
        elif s["id"] == "products":
            related = "\n".join(f"{n}. {p['text']}" for n, p in enumerate(s["related"], start=1))
            other = "\n".join(f"{n}. {p['text']}" for n, p in enumerate(s["other"], start=1))
            text = (
                f"Products closely related to the proposed project:\n{related or '(none)'}\n\n"
                f"Other significant products:\n{other or '(none)'}"
            )
        elif s["id"] == "honors":
            text = "\n".join(
                " | ".join(x for x in (r["year"], r["title"], r["organization"]) if x)
                for r in s["rows"]
            )
        elif s["id"] == "contributions":
            text = "\n\n".join(f"{n}. {c}" for n, c in enumerate(s["items"], start=1))
        else:
            text = s.get("text", "")
        out.append({"id": s["id"], "title": s["title"], "form": s["form"], "text": text})
    return out


def markdown(name: str, built: list[dict[str, Any]]) -> str:
    who = built[0]["rows"][0]["value"]
    lines = [
        f"# {who}",
        "",
        f"NIH biosketch: {name}",
        "",
        f"Form: {FORM_LABEL}.",
        "Complete and certify it in SciENcv; this is the content, prepared to paste.",
        "",
    ]
    current = None
    for part in sciencv(built):
        if part["form"] != current:
            current = part["form"]
            lines += [
                "",
                "## Biographical Sketch Common Form"
                if current == "common"
                else "## NIH Biographical Sketch Supplement",
            ]
        lines += ["", f"### {part['title']}", "", part["text"] or "(empty)"]
    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "FORM",
    "FORM_LABEL",
    "LIMITS",
    "build",
    "checks",
    "citations_in",
    "clean_spec",
    "default_spec",
    "markdown",
    "sciencv",
]
