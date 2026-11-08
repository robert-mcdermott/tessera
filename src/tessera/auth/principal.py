"""Who is asking, and what they may do.

A principal is built for every request: from the signed-in account and its grants, or, in
single-operator mode (no accounts, this machine only), as the configured operator with every
permission. Decisions are recorded under ``principal.name``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from tessera.auth.accounts import Grant
from tessera.evidence.memberships import is_current
from tessera.evidence.store import survivor_of
from tessera.ledger.tables import assertion, unit


@dataclass
class Principal:
    name: str  # recorded as the actor on decisions
    account_id: str | None = None
    username: str | None = None
    person_id: str | None = None  # a researcher's own person
    grants: tuple[Grant, ...] = ()
    single_operator: bool = False
    csrf_token: str | None = None
    _people: Any = field(default=False, repr=False)  # cache; False until computed

    @property
    def roles(self) -> set[str]:
        return {"admin"} if self.single_operator else {g.role for g in self.grants}

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles

    @property
    def curates_all(self) -> bool:
        """May decide for anyone, including discovered people and duplicates."""
        return self.is_admin or any(g.role == "curator" and not g.scope_type for g in self.grants)

    @property
    def curates_any(self) -> bool:
        """Holds a curator role, perhaps for some people only."""
        return self.is_admin or any(g.role == "curator" for g in self.grants)

    @property
    def reviews(self) -> bool:
        """May decide something: a curator, or a researcher about their own person."""
        return self.curates_any or ("researcher" in self.roles and bool(self.person_id))

    def curated_people(self, engine: Engine) -> set[str] | None:
        """The people this principal may decide for; None means everyone."""
        if self.curates_all:
            return None
        if self._people is not False:
            return self._people
        people: set[str] = set()
        with engine.connect() as conn:
            if "researcher" in self.roles and self.person_id:
                people.add(survivor_of(conn, self.person_id))
            units: set[str] = set()
            for g in self.grants:
                if g.role != "curator" or not g.scope_id:
                    continue
                if g.scope_type == "person":
                    people.add(survivor_of(conn, g.scope_id))
                elif g.scope_type == "unit":
                    units.add(g.scope_id)
            if units:
                children: dict[str, list[str]] = defaultdict(list)
                for uid, parent in conn.execute(select(unit.c.id, unit.c.parent_id)):
                    if parent:
                        children[parent].append(uid)
                stack = list(units)
                while stack:  # a unit's scope includes the units under it
                    for child in children.get(stack.pop(), []):
                        if child not in units:
                            units.add(child)
                            stack.append(child)
                today = date.today()
                for subject, qualifiers in conn.execute(
                    select(assertion.c.subject_id, assertion.c.qualifiers).where(
                        assertion.c.predicate == "member_of",
                        assertion.c.status == "accepted",
                        assertion.c.object_id.in_(units),
                    )
                ):
                    if is_current(qualifiers, today):
                        people.add(subject)
        self._people = people
        return people

    def can_curate(self, engine: Engine, person_id: str) -> bool:
        scope = self.curated_people(engine)
        return scope is None or person_id in scope

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.account_id,
            "username": self.username,
            "name": self.name,
            "person_id": self.person_id,
            "single_operator": self.single_operator,
            "roles": [g.to_json() for g in self.grants]
            if not self.single_operator
            else [{"role": "admin", "scope_type": None, "scope_id": None}],
            "permissions": {
                "admin": self.is_admin,
                "curate_all": self.curates_all,
                "curate_any": self.curates_any,
                "review": self.reviews,
            },
        }


__all__ = ["Principal"]
