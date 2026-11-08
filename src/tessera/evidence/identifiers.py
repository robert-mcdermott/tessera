"""Identifiers suggested by the evidence, for a curator to confirm.

An ORCID iD printed at a person's author position on several of their own papers (accepted
ones, or strong suggestions) is very likely theirs. Tessera proposes it as a *suggested*
identifier and never accepts it on its own: a curator confirms it with one click, after
which the iD counts as decisive evidence and the person's public ORCID record is read.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.engine import Engine

from tessera.connectors.orcid import orcid_checksum_ok
from tessera.evidence import store
from tessera.ledger.tables import person_identifier

PROPOSE_MIN_PAPERS = 2  # an iD on one paper may be a publisher's slip; two is a pattern
SOURCE = "papers"


@dataclass
class ProposalStats:
    proposed: int = 0
    withdrawn: int = 0
    held_by_someone_else: int = 0
    added: list[tuple[str, str]] = field(default_factory=list)  # (person, iD) proposed now

    def to_json(self) -> dict[str, Any]:
        return {k: v for k, v in vars(self).items() if k != "added"}


def propose_orcids(engine: Engine, support: dict[str, Counter]) -> ProposalStats:
    """Suggest iDs with enough support; withdraw earlier suggestions that lost it.

    ``support`` maps a person to how many of their likely papers carry each iD at their
    author position. Accepted and rejected identifiers are never touched, and an iD that a
    curator gave to someone else is not proposed (the scoring treats it as a conflict).
    """
    stats = ProposalStats()
    with engine.begin() as conn:
        rows = conn.execute(
            select(person_identifier).where(person_identifier.c.scheme == "orcid")
        ).mappings()
        held: dict[str, set[str]] = defaultdict(set)
        accepted_by: dict[str, set[str]] = defaultdict(set)
        stale = []
        for r in rows:
            held[r["person_id"]].add(r["value"])
            if r["status"] == "accepted":
                accepted_by[r["value"]].add(r["person_id"])
            elif (
                r["status"] == "suggested"
                and r["source"] == SOURCE
                and support.get(r["person_id"], Counter())[r["value"]] < PROPOSE_MIN_PAPERS
            ):
                stale.append(r["id"])
        if stale:
            conn.execute(delete(person_identifier).where(person_identifier.c.id.in_(stale)))
            stats.withdrawn = len(stale)
        for person_id in sorted(support):
            for orcid, papers in sorted(support[person_id].items()):
                if papers < PROPOSE_MIN_PAPERS or orcid in held[person_id]:
                    continue
                if not orcid_checksum_ok(orcid):  # a mistyped iD on the papers
                    continue
                if accepted_by[orcid] - {person_id}:
                    stats.held_by_someone_else += 1
                    continue
                store.add_identifier(
                    conn, person_id, "orcid", orcid, source=SOURCE, status="suggested"
                )
                stats.proposed += 1
                stats.added.append((person_id, orcid))
    return stats


__all__ = ["PROPOSE_MIN_PAPERS", "ProposalStats", "propose_orcids"]
