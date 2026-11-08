"""Safe auto-accept (CUR-05): off by default, gated by measured precision, undone in bulk.

An admin may switch it on only at a threshold where the latest evaluation of the active model,
on people's own decisions, shows at least ``PRECISION_BAR`` precision over at least
``MIN_SUPPORT`` decisions. Each resolve then accepts the open suggestions at or above the
threshold that carry no conflicting evidence, records them as one batch under the actor
``Auto-accept``, and never uses them as training data. A batch can be undone together.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from tessera.config import Config
from tessera.core.clock import isoformat, utc_now
from tessera.core.errors import TesseraError
from tessera.core.ids import new_id
from tessera.evidence import store
from tessera.evidence.model import (
    AUTO_ACTOR,
    EVALUATION_SETTING,
    active_model,
    read_setting,
    write_setting,
)
from tessera.ledger.tables import assertion, auto_accept_batch, person

SETTING = "auto_accept"
PRECISION_BAR = 0.99
MIN_SUPPORT = 50
# Evidence that something is wrong: never accepted automatically, whatever the score.
CONFLICTS = frozenset(
    {
        "orcid_conflict",
        "orcid_rejected",
        "orcid_inconsistent",
        "given_conflict",
        "middle_conflict",
        "slot_conflict",
        "career_window",
        "rejected_coauthors",
    }
)


class AutoAcceptError(TesseraError):
    """Auto-accept cannot be switched on as asked."""


def status(engine: Engine) -> dict[str, Any]:
    current = read_setting(engine, SETTING) or {}
    return {
        "enabled": bool(current.get("enabled")),
        "threshold": current.get("threshold"),
        "precision": current.get("precision"),
        "support": current.get("support"),
        "method": current.get("method"),
        "enabled_by": current.get("enabled_by"),
        "enabled_at": current.get("enabled_at"),
        "disabled_because": current.get("disabled_because"),
        "precision_bar": PRECISION_BAR,
        "min_support": MIN_SUPPORT,
    }


def measured_precision(engine: Engine, threshold: float) -> tuple[float | None, int]:
    """Precision and support at a threshold for the active model, from the latest evaluation."""
    report = read_setting(engine, EVALUATION_SETTING)
    model = active_model(engine)
    if not report:
        raise AutoAcceptError("Evaluate the model first: there is no evaluation yet.")
    if model.is_base:
        measures = report.get("base")
    elif (report.get("candidate") or {}).get("method") == model.method:
        measures = report.get("fitted")
    else:
        measures = None
    if not measures:
        raise AutoAcceptError(
            "The latest evaluation does not cover the active model; evaluate it again."
        )
    point = next((p for p in measures["curve"] if abs(p["threshold"] - threshold) < 1e-9), None)
    if point is None:
        allowed = ", ".join(str(p["threshold"]) for p in measures["curve"])
        raise AutoAcceptError(f"Choose a threshold the evaluation measured: {allowed}.")
    return point["precision"], point["support"]


def enable(engine: Engine, threshold: float, actor: str) -> dict[str, Any]:
    precision, support = measured_precision(engine, threshold)
    if support < MIN_SUPPORT:
        raise AutoAcceptError(
            f"Only {support} decisions scored at or above {threshold}; at least {MIN_SUPPORT} "
            "are needed to trust the measured precision."
        )
    if precision is None or precision < PRECISION_BAR:
        shown = "unknown" if precision is None else f"{precision:.1%}"
        raise AutoAcceptError(
            f"At {threshold} the evaluated precision is {shown}, below the "
            f"{PRECISION_BAR:.0%} required."
        )
    value = {
        "enabled": True,
        "threshold": threshold,
        "precision": precision,
        "support": support,
        "method": active_model(engine).method,
        "enabled_by": actor,
        "enabled_at": isoformat(utc_now()),
    }
    write_setting(engine, SETTING, value, actor)
    return status(engine)


def disable(engine: Engine, actor: str) -> dict[str, Any]:
    current = read_setting(engine, SETTING) or {}
    write_setting(engine, SETTING, {**current, "enabled": False, "disabled_because": None}, actor)
    return status(engine)


def run(engine: Engine, config: Config) -> dict[str, Any]:
    """Accept what qualifies, as one batch. Called at the end of every resolve."""
    current = status(engine)
    model = active_model(engine)
    if not current["enabled"]:
        return {"enabled": False, "accepted": 0}
    if current["method"] != model.method:  # measured for another model: stop
        disable(engine, AUTO_ACTOR)
        return {"enabled": False, "accepted": 0, "stopped": "the model changed"}
    threshold = float(current["threshold"])
    with engine.connect() as conn:
        directory = set(
            conn.execute(select(person.c.id).where(person.c.status.in_(("active", "former"))))
            .scalars()
            .all()
        )
    chosen = [
        r
        for r in store.assertions_for(
            engine, predicate="authored", statuses=("suggested",), min_score=threshold
        )
        if not r["locked"]
        and r["subject_id"] in directory
        and r["method"] == model.method
        and not ({f["feature"] for f in r["evidence"]} & CONFLICTS)
    ]
    if not chosen:
        return {"enabled": True, "accepted": 0}
    batch_id = new_id("bat")
    note = (
        f"Accepted automatically: scored at least {threshold} by {model.method}, where "
        f"evaluated precision is {current['precision']:.1%} (batch {batch_id})"
    )
    done = [store.decide(engine, r["id"], "accept", AUTO_ACTOR, note)["id"] for r in chosen]
    with engine.begin() as conn:
        conn.execute(
            insert(auto_accept_batch).values(
                id=batch_id,
                created_at=utc_now(),
                threshold=threshold,
                precision=current["precision"],
                method=model.method,
                assertion_ids=done,
            )
        )
    return {"enabled": True, "accepted": len(done), "batch": batch_id}


def batches(engine: Engine, limit: int = 20) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(auto_accept_batch).order_by(auto_accept_batch.c.created_at.desc()).limit(limit)
        ).mappings()
        return [
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "threshold": r["threshold"],
                "precision": r["precision"],
                "method": r["method"],
                "accepted": len(r["assertion_ids"]),
                "undone_at": r["undone_at"],
                "undone_by": r["undone_by"],
            }
            for r in rows
        ]


def undo_batch(engine: Engine, batch_id: str, actor: str) -> int:
    """Return a batch's links to suggestions, except any a person has decided since, and
    switch auto-accept off: whoever undoes a batch has seen a reason to stop it, and the next
    sync would otherwise accept the same links again."""
    with engine.connect() as conn:
        row = (
            conn.execute(select(auto_accept_batch).where(auto_accept_batch.c.id == batch_id))
            .mappings()
            .first()
        )
        if row is None:
            raise AutoAcceptError(f"No batch {batch_id}")
        if row["undone_at"] is not None:
            raise AutoAcceptError("This batch has already been undone.")
        still = set(
            conn.execute(
                select(assertion.c.id).where(
                    assertion.c.id.in_(row["assertion_ids"]),
                    assertion.c.status == "accepted",
                    assertion.c.decided_by == AUTO_ACTOR,
                )
            )
            .scalars()
            .all()
        )
    for assertion_id in row["assertion_ids"]:
        if assertion_id in still:
            store.decide(engine, assertion_id, "undo", actor, f"Undid auto-accept batch {batch_id}")
    with engine.begin() as conn:
        conn.execute(
            update(auto_accept_batch)
            .where(auto_accept_batch.c.id == batch_id)
            .values(undone_at=utc_now(), undone_by=actor)
        )
    current = read_setting(engine, SETTING) or {}
    if current.get("enabled"):
        why = f"batch {batch_id} was undone"
        write_setting(
            engine, SETTING, {**current, "enabled": False, "disabled_because": why}, actor
        )
    return len(still)


__all__ = [
    "AUTO_ACTOR",
    "MIN_SUPPORT",
    "PRECISION_BAR",
    "SETTING",
    "AutoAcceptError",
    "batches",
    "disable",
    "enable",
    "measured_precision",
    "run",
    "status",
    "undo_batch",
]
