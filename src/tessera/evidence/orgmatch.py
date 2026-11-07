"""Recognize the organization in free text: affiliations, sponsor names, site names.

Built from the organization profile in the configuration. Matching is
case-insensitive, accent-insensitive, and punctuation-tolerant, at word boundaries.
Exclusions win: "Example University of Technology" is not "Example University".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tessera.config import OrganizationConfig
from tessera.evidence.names import fold

EMAIL = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", re.IGNORECASE)


def _normalize(text: str) -> str:
    text = fold(text).replace("&", " and ")
    text = re.sub(r"[^a-z0-9@.\-]+", " ", text)
    text = re.sub(r"(?<![a-z0-9])\.|\.(?![a-z0-9])", " ", text)  # dots that end words
    return " ".join(text.replace("-", " ").split())


_WILDCARD = "qqwildcardqq"  # survives normalization, unlike "*"


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    words = _normalize(phrase.replace("*", _WILDCARD)).split()
    parts = [r"\S*".join(re.escape(piece) for piece in word.split(_WILDCARD)) for word in words]
    return re.compile(r"(?<![a-z0-9])" + r"\s+".join(parts) + r"(?![a-z0-9])")


@dataclass(frozen=True)
class OrgMatch:
    phrase: str  # the configured alias, pattern, or email domain that matched
    kind: str  # "alias" | "pattern" | "email"


class OrgMatcher:
    def __init__(self, org: OrganizationConfig) -> None:
        self.name = org.name
        names = [org.name, *org.aliases, *org.reporter_org_names, *org.ctgov_org_names]
        self._aliases = [(n, _phrase_pattern(n)) for n in dict.fromkeys(names) if n.strip()]
        self._patterns = [(p, _phrase_pattern(p)) for p in org.affiliation_patterns if p.strip()]
        self._exclusions = [_phrase_pattern(e) for e in org.exclusions if e.strip()]
        self._domains = tuple(d.lower().lstrip("@") for d in org.email_domains)

    def match(self, text: str | None) -> OrgMatch | None:
        """The first configured name found in ``text``, unless an exclusion covers it."""
        if not text:
            return None
        for domain in self._email_domains(text):
            return OrgMatch(domain, "email")
        normalized = _normalize(text)
        excluded_spans = [m.span() for pat in self._exclusions for m in pat.finditer(normalized)]
        for kind, candidates in (("alias", self._aliases), ("pattern", self._patterns)):
            for phrase, pattern in candidates:
                for found in pattern.finditer(normalized):
                    if not any(s <= found.start() and found.end() <= e for s, e in excluded_spans):
                        return OrgMatch(phrase, kind)
        return None

    def matches(self, text: str | None) -> bool:
        return self.match(text) is not None

    def match_any(self, texts: list[str] | None) -> tuple[OrgMatch, str] | None:
        """The first matching text of several (e.g. one author's affiliations)."""
        for text in texts or []:
            found = self.match(text)
            if found:
                return found, text
        return None

    def _email_domains(self, text: str) -> list[str]:
        if not self._domains:
            return []
        found = []
        for match in EMAIL.finditer(text):
            domain = match.group(1).lower().rstrip(".")
            if any(domain == d or domain.endswith("." + d) for d in self._domains):
                found.append(domain)
        return found


def emails_in(text: str | None) -> list[str]:
    """Email addresses written in an affiliation string, lowercased."""
    return [m.group(0).lower().rstrip(".") for m in EMAIL.finditer(text or "")]
