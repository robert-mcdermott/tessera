"""Person names: normalization, parsing, and compatibility.

Names are the weakest identity signal, so they are compared for *compatibility*, not
equality: "A" is compatible with "Ada"; "Ada B" conflicts with "Ada C"; "Ada" conflicts
with "Anna". Tessera never infers gender, ethnicity, or nationality from names.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

# Letters that Unicode decomposition does not reduce to ASCII.
_TRANSLATE = str.maketrans(
    {"ø": "o", "Ø": "O", "ł": "l", "Ł": "L", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
     "ß": "ss", "đ": "d", "Đ": "D", "þ": "th", "Þ": "TH", "\u0131": "i"}
)  # fmt: skip
PARTICLES = frozenset({"van", "von", "der", "den", "de", "del", "della", "di", "da", "dos",
                       "das", "du", "le", "la", "st", "ter", "ten", "bin", "al", "el"})  # fmt: skip
SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})
CREDENTIALS = frozenset(
    {"md", "phd", "mph", "ms", "msc", "mba", "rn", "np", "pa", "do", "dds", "dmd", "pharmd",
     "facs", "facp", "frcpc", "frcp", "mbbs", "mbchb", "bsc", "ba", "bs", "ma", "dr",
     "prof", "professor", "mr", "mrs", "mx", "drph", "scd", "mhs", "mspH", "faan"}
)  # fmt: skip


def fold(text: str) -> str:
    """Lowercase ASCII with accents and special letters folded: "Sørensen-Ölz" -> "sorensen-olz"."""
    decomposed = unicodedata.normalize("NFKD", text.translate(_TRANSLATE))
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.encode("ascii", "ignore").decode().lower()


def key(text: str | None) -> str:
    """A comparison key: folded letters only. "van der Berg" -> "vanderberg"."""
    return re.sub(r"[^a-z]", "", fold(text or ""))


def family_keys(family: str | None) -> set[str]:
    """Keys a family name may appear under: whole, without particles, and each hyphen part."""
    if not family:
        return set()
    words = re.split(r"[\s]+", fold(family).strip())
    keys = {key(family)}
    core = [w for w in words if w not in PARTICLES]
    if core and len(core) < len(words):
        keys.add(key(" ".join(core)))
    for part in re.split(r"[-\s]+", fold(family)):
        if len(key(part)) >= 3 and "-" in family:
            keys.add(key(part))
    keys.discard("")
    return keys


@dataclass(frozen=True)
class Name:
    family: str
    given: str = ""  # first and middle names as written, possibly just initials
    initials: str = ""  # uppercase initials of given names, e.g. "AB"
    given_is_initials: bool = False  # the given field holds only initials (e.g. "AB")

    @property
    def family_key(self) -> str:
        return key(self.family)

    @property
    def first_initial(self) -> str:
        return self.initials[:1]

    @property
    def given_tokens(self) -> list[str]:
        return [t for t in re.split(r"[\s.\-]+", fold(self.given)) if t]

    @property
    def first_given(self) -> str:
        """The first given name if written in full (more than one letter), else ''."""
        if self.given_is_initials:
            return ""
        tokens = self.given_tokens
        return tokens[0] if tokens and len(tokens[0]) > 1 else ""

    @property
    def display(self) -> str:
        return f"{self.given} {self.family}".strip()


def initials_of(given: str | None) -> str:
    """ "Ada Beth" -> "AB"; "A.B." -> "AB"; "Jean-Luc" -> "JL"; "AB" (already initials) -> "AB"."""
    if not given:
        return ""
    tokens = [t for t in re.split(r"[\s.\-]+", fold(given)) if t]
    return "".join(t[0] for t in tokens).upper()


def make_name(
    family: str | None, given: str | None = None, initials: str | None = None
) -> Name | None:
    if not family or not key(family):
        return None
    given = " ".join((given or "").split())
    computed = initials_of(given)
    explicit = re.sub(r"[^A-Za-z]", "", initials or "").upper()
    # A given field that just repeats the initials ("AB" / "A B" / "A.B.") holds no names.
    is_initials = bool(explicit) and re.sub(r"[^A-Za-z]", "", given).upper() == explicit
    return Name(" ".join(family.split()), given, explicit or computed, is_initials)


def _strip_credentials(tokens: list[str]) -> list[str]:
    while tokens and key(tokens[-1]) in CREDENTIALS | SUFFIXES:
        tokens = tokens[:-1]
    while tokens and key(tokens[0]) in {"dr", "prof", "professor", "mr", "mrs", "ms", "mx"}:
        tokens = tokens[1:]
    return tokens


def parse_display_name(text: str | None) -> Name | None:
    """Parse "Ada B. Okafor, MD, PhD" or "Okafor, Ada B" into a Name."""
    if not text or not text.strip():
        return None
    text = " ".join(text.split())
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) >= 2 and not all(key(p) in CREDENTIALS | SUFFIXES for p in parts[1:]):
        # "Okafor, Ada B" (but not "Ada Okafor, MD")
        family, given = parts[0], parts[1]
        given_tokens = _strip_credentials(given.split())
        return make_name(family, " ".join(given_tokens))
    tokens = _strip_credentials(parts[0].split())
    if not tokens:
        return None
    if len(tokens) == 1:
        return make_name(tokens[0])
    # Keep lowercase particles with the family name: "Ana de la Cruz".
    split = len(tokens) - 1
    while split > 1 and fold(tokens[split - 1]) in PARTICLES:
        split -= 1
    return make_name(" ".join(tokens[split:]), " ".join(tokens[:split]))


class Match(Enum):
    FULL = "full"  # first given names written in full and equal
    INITIALS = "initials"  # compatible, but at least one side gives only initials
    GIVEN_CONFLICT = "given_conflict"  # same first initial, different full first names
    MIDDLE_CONFLICT = "middle_conflict"  # first names agree, middle initials differ
    NONE = "none"  # different family name or first initial


def compare(a: Name, b: Name) -> Match:
    """How well two names agree. Family names must share a key; first initials must agree."""
    if not (family_keys(a.family) & family_keys(b.family)):
        return Match.NONE
    if not a.first_initial or not b.first_initial:
        return Match.INITIALS  # a family name alone is weakly compatible with anything
    if a.first_initial != b.first_initial:
        return Match.NONE
    a_first, b_first = a.first_given, b.first_given
    if a_first and b_first and a_first != b_first:
        return Match.GIVEN_CONFLICT
    a_mid, b_mid = a.initials[1:2], b.initials[1:2]
    if a_mid and b_mid and a_mid != b_mid:
        return Match.MIDDLE_CONFLICT
    if a_first and b_first:
        return Match.FULL
    return Match.INITIALS


def best_match(candidates: list[Name], other: Name) -> Match:
    """The best agreement between ``other`` and any of a person's name variants."""
    order = [Match.FULL, Match.INITIALS, Match.MIDDLE_CONFLICT, Match.GIVEN_CONFLICT, Match.NONE]
    results = {compare(name, other) for name in candidates}
    return next(m for m in order if m in results)
