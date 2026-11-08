"""Learned authorship weights (``authorship/v2``), fitted to this organization's decisions.

The hand-set model (``authorship/v1.2``) adds up evidence, each item with a fixed weight. The
learned model keeps every feature and its explanation, and learns one *multiplier* per
feature plus an offset: ``score = sigmoid(offset + prior + sum(multiplier * hand-set
weight))``. With every multiplier at 1 and no offset it is the hand-set model, and fitting is
pulled toward exactly that (an L2 penalty on the distance from 1), so a handful of decisions
barely moves it while thousands can say "coauthors matter more here than we assumed".

Fitting is logistic regression by Newton's method in plain Python: there are a few dozen
parameters and each example has only a handful of features, so it needs no numerical
library. A model is evaluated on people it was not fitted to (folds by person, not by paper)
and released only if it beats the hand-set model there.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.engine import Engine

from tessera.config import Config
from tessera.core.clock import isoformat, utc_now
from tessera.core.errors import TesseraError
from tessera.evidence import store
from tessera.evidence.authorship import METHOD as BASE_METHOD
from tessera.ledger.tables import setting

MODEL_SETTING = "authorship_model"
EVALUATION_SETTING = "authorship_evaluation"
AUTO_ACTOR = "Auto-accept"  # decided_by on links accepted automatically; never training data
FOLDS = 5
L2 = 20.0  # pull toward the hand-set weights: 30 decisions move a multiplier by < 0.2
MULTIPLIER_RANGE = (0.0, 4.0)  # learning may weaken or strengthen evidence, never reverse it
OFFSET_RANGE = (-4.0, 4.0)
MIN_EXAMPLES = 200
MIN_NEGATIVES = 20
MIN_PEOPLE = 5
CURVE = (0.9, 0.95, 0.97, 0.98, 0.99, 0.995, 0.999)
FIXED = frozenset({"prior", "model_offset"})  # not scaled: the offset is learned separately


class ModelError(TesseraError):
    """A model request that cannot be done as asked."""


@dataclass
class Model:
    method: str = BASE_METHOD
    multipliers: dict[str, float] = field(default_factory=dict)
    offset: float = 0.0
    fitted_at: str | None = None
    trained_on: dict[str, Any] | None = None

    @property
    def is_base(self) -> bool:
        return not self.multipliers and not self.offset

    def scale(self, feature: str) -> float:
        return 1.0 if feature in FIXED else self.multipliers.get(feature, 1.0)

    def apply(self, features: list[dict[str, Any]]) -> float:
        """Rewrite evidence weights in place (keeping the hand-set one) and return the total."""
        if self.is_base:
            return sum(f["weight"] or 0 for f in features)
        total = 0.0
        for f in features:
            if f["feature"] == "model_offset":
                continue
            base = f.setdefault("base_weight", f["weight"])
            if base is None:
                continue
            f["weight"] = round(base * self.scale(f["feature"]), 3)
            total += f["weight"]
        if self.offset:
            features.append(
                {
                    "feature": "model_offset",
                    "value": None,
                    "weight": round(self.offset, 3),
                    "base_weight": 0.0,
                    "explanation": "Adjustment learned from this organization's decisions",
                    "source_ref": "model",
                }
            )
            total += self.offset
        return total

    def to_json(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "multipliers": dict(sorted(self.multipliers.items())),
            "offset": self.offset,
            "fitted_at": self.fitted_at,
            "trained_on": self.trained_on,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Model:
        return cls(
            method=data["method"],
            multipliers={k: float(v) for k, v in (data.get("multipliers") or {}).items()},
            offset=float(data.get("offset") or 0.0),
            fitted_at=data.get("fitted_at"),
            trained_on=data.get("trained_on"),
        )


# --- settings ---------------------------------------------------------------------------------


def read_setting(engine: Engine, key: str) -> dict[str, Any] | None:
    with engine.connect() as conn:
        return conn.execute(select(setting.c.value).where(setting.c.key == key)).scalar()


def write_setting(engine: Engine, key: str, value: dict[str, Any] | None, actor: str) -> None:
    with engine.begin() as conn:
        conn.execute(delete(setting).where(setting.c.key == key))
        if value is not None:
            conn.execute(
                insert(setting).values(key=key, value=value, updated_by=actor, updated_at=utc_now())
            )


def active_model(engine: Engine) -> Model:
    """The released learned model, or the hand-set one."""
    data = read_setting(engine, MODEL_SETTING)
    return Model.from_json(data) if data else Model()


# --- training data ----------------------------------------------------------------------------


@dataclass
class Example:
    assertion_id: str
    person_id: str
    label: bool
    x: dict[str, float]  # hand-set contribution of each feature, prior included

    @property
    def base_total(self) -> float:
        return sum(self.x.values())


def contributions(evidence: list[dict[str, Any]]) -> dict[str, float]:
    x: dict[str, float] = defaultdict(float)
    for item in evidence or []:
        if item["feature"] == "model_offset":
            continue
        weight = item.get("base_weight", item.get("weight"))
        if weight is not None:
            x[item["feature"]] += float(weight)
    return dict(x)


def examples_from_ledger(engine: Engine) -> list[Example]:
    """People's decisions on current authorship suggestions. Automatic acceptances are not
    labels: learning from them would only teach the model to agree with itself."""
    out = []
    for row in store.assertions_for(engine, predicate="authored"):
        if not row["locked"] or row["status"] not in ("accepted", "rejected"):
            continue
        if row["decided_by"] == AUTO_ACTOR or not str(row["method"]).startswith("authorship/"):
            continue
        x = contributions(row["evidence"])
        if not x:
            continue
        out.append(Example(row["id"], row["subject_id"], row["status"] == "accepted", x))
    return out


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def predict(model: Model, x: dict[str, float]) -> float:
    return sigmoid(model.offset + sum(v * model.scale(f) for f, v in x.items()))


# --- fitting ----------------------------------------------------------------------------------


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (the matrix is small and well conditioned)."""
    n = len(b)
    m = [[*row, b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[pivot] = m[pivot], m[col]
        if abs(m[col][col]) < 1e-12:
            continue
        for r in range(col + 1, n):
            factor = m[r][col] / m[col][col]
            if factor:
                for c in range(col, n + 1):
                    m[r][c] -= factor * m[col][c]
    out = [0.0] * n
    for r in range(n - 1, -1, -1):
        if abs(m[r][r]) < 1e-12:
            continue
        out[r] = (m[r][n] - sum(m[r][c] * out[c] for c in range(r + 1, n))) / m[r][r]
    return out


def fit(
    examples: list[Example], *, l2: float = L2, method: str = "", iterations: int = 50
) -> Model:
    """Penalized logistic regression on the multipliers and the offset."""
    names = sorted({f for e in examples for f in e.x} - FIXED)
    index = {f: i + 1 for i, f in enumerate(names)}  # 0 is the offset
    k = len(names) + 1
    theta = [0.0] + [1.0] * len(names)
    target = theta[:]
    rows = [
        (
            sum(v for f, v in e.x.items() if f in FIXED),
            [(index[f], v) for f, v in e.x.items() if f not in FIXED],
            1.0 if e.label else 0.0,
        )
        for e in examples
    ]
    for _ in range(iterations):
        grad = [2 * l2 * (theta[i] - target[i]) for i in range(k)]
        hess = [[(2 * l2 if i == j else 0.0) for j in range(k)] for i in range(k)]
        for fixed, sparse, y in rows:
            z = fixed + theta[0] + sum(theta[i] * v for i, v in sparse)
            p = sigmoid(z)
            w = p * (1 - p)
            terms = [(0, 1.0), *sparse]
            for i, vi in terms:
                grad[i] += (p - y) * vi
                for j, vj in terms:
                    hess[i][j] += w * vi * vj
        step = _solve(hess, grad)
        theta = [t - s for t, s in zip(theta, step, strict=True)]
        theta[0] = min(max(theta[0], OFFSET_RANGE[0]), OFFSET_RANGE[1])
        for i in range(1, k):
            theta[i] = min(max(theta[i], MULTIPLIER_RANGE[0]), MULTIPLIER_RANGE[1])
        if max(abs(s) for s in step) < 1e-6:
            break
    return Model(
        method=method,
        multipliers={f: round(theta[index[f]], 4) for f in names},
        offset=round(theta[0], 4),
        fitted_at=isoformat(utc_now()),
        trained_on={
            "examples": len(examples),
            "positives": sum(e.label for e in examples),
            "people": len({e.person_id for e in examples}),
        },
    )


# --- evaluation -------------------------------------------------------------------------------


def _fold(person_id: str) -> int:
    return int(hashlib.sha256(person_id.encode()).hexdigest(), 16) % FOLDS


def _measures(pairs: list[tuple[float, bool]], config: Config) -> dict[str, Any]:
    n = len(pairs)
    eps = 1e-9
    log_loss = -sum(math.log(p + eps) if y else math.log(1 - p + eps) for p, y in pairs) / n
    brier = sum((p - (1.0 if y else 0.0)) ** 2 for p, y in pairs) / n
    positives = sum(y for _, y in pairs)

    def at(threshold: float) -> dict[str, Any]:
        chosen = [y for p, y in pairs if p >= threshold]
        hits = sum(chosen)
        return {
            "threshold": threshold,
            "support": len(chosen),
            "precision": round(hits / len(chosen), 4) if chosen else None,
            "recall": round(hits / positives, 4) if positives else None,
        }

    calibration = []
    edges = (0.0, 0.2, 0.4, 0.6, 0.8, 0.95, 1.01)
    for low, high in pairwise(edges):
        bucket = [(p, y) for p, y in pairs if low <= p < high]
        if bucket:
            calibration.append(
                {
                    "range": [low, min(high, 1.0)],
                    "examples": len(bucket),
                    "mean_score": round(sum(p for p, _ in bucket) / len(bucket), 4),
                    "fraction_correct": round(sum(y for _, y in bucket) / len(bucket), 4),
                }
            )
    return {
        "log_loss": round(log_loss, 4),
        "brier": round(brier, 4),
        "strong": at(config.evidence.strong_threshold),
        "review": at(config.evidence.review_threshold),
        "curve": [at(t) for t in CURVE],
        "calibration": calibration,
    }


def evaluate(engine: Engine, config: Config, *, actor: str = "command line") -> dict[str, Any]:
    """Evaluate on this organization's decisions and keep the result as the latest evaluation."""
    report = evaluate_examples(examples_from_ledger(engine), config, active_model(engine))
    write_setting(engine, EVALUATION_SETTING, report, actor)
    return report


def evaluate_examples(
    examples: list[Example], config: Config, active: Model | None = None
) -> dict[str, Any]:
    """Fit on all but one fold of people at a time, score the fold left out, and compare with
    the hand-set model on the same decisions."""
    active = active or Model()
    people = {e.person_id for e in examples}
    negatives = sum(not e.label for e in examples)
    report: dict[str, Any] = {
        "generated_at": isoformat(utc_now()),
        "active_method": active.method,
        "base_method": BASE_METHOD,
        "decisions": {
            "examples": len(examples),
            "positives": len(examples) - negatives,
            "negatives": negatives,
            "people": len(people),
        },
        "minimum": {"examples": MIN_EXAMPLES, "negatives": MIN_NEGATIVES, "people": MIN_PEOPLE},
        "base": None,
        "fitted": None,
        "active": None,
        "candidate": None,
        "better": False,
        "reasons": [],
    }
    if not examples:
        report["reasons"].append(
            "No decisions yet: curators' accept and reject decisions are the data."
        )
        return report
    base = Model()
    report["base"] = _measures([(predict(base, e.x), e.label) for e in examples], config)
    report["active"] = (
        report["base"]
        if active.is_base
        else _measures([(predict(active, e.x), e.label) for e in examples], config)
    )
    if len(examples) < MIN_EXAMPLES:
        report["reasons"].append(f"Only {len(examples)} decisions; fitting needs {MIN_EXAMPLES}.")
    if negatives < MIN_NEGATIVES:
        report["reasons"].append(f"Only {negatives} rejections; fitting needs {MIN_NEGATIVES}.")
    if len(people) < MIN_PEOPLE:
        report["reasons"].append(
            f"Decisions cover {len(people)} people; fitting needs {MIN_PEOPLE}."
        )
    if report["reasons"]:
        return report

    held_out: list[tuple[float, bool]] = []
    base_held: list[tuple[float, bool]] = []
    for fold in range(FOLDS):
        train = [e for e in examples if _fold(e.person_id) != fold]
        test = [e for e in examples if _fold(e.person_id) == fold]
        if not test or not train:
            continue
        model = fit(train)
        held_out += [(predict(model, e.x), e.label) for e in test]
        base_held += [(predict(base, e.x), e.label) for e in test]
    fitted = _measures(held_out, config)
    base_measures = _measures(base_held, config)
    report["base"], report["fitted"] = base_measures, fitted
    stamp = utc_now().strftime("%Y%m%d-%H%M")
    candidate = fit(examples, method=f"authorship/v2-{stamp}")
    report["candidate"] = candidate.to_json()
    base_strong = base_measures["strong"]["precision"] or 0.0
    fitted_strong = fitted["strong"]["precision"] or 0.0
    if fitted["log_loss"] > base_measures["log_loss"] * 0.99:
        report["reasons"].append(
            "On people it was not fitted to, the learned model does not predict decisions "
            f"better (log loss {fitted['log_loss']} against {base_measures['log_loss']})."
        )
    if fitted_strong < base_strong:
        report["reasons"].append(
            f"Its strong band is less precise ({fitted_strong:.1%} against {base_strong:.1%})."
        )
    report["better"] = not report["reasons"]
    return report


def release(engine: Engine, actor: str) -> Model:
    """Make the latest evaluation's learned model active, if it beat the hand-set model.

    Scores change at the next resolve or sync. Auto-accept is switched off, because its
    precision was measured for the previous model."""
    report = read_setting(engine, EVALUATION_SETTING)
    if not report or not report.get("candidate"):
        raise ModelError("There is no learned model to release; run an evaluation first.")
    if not report.get("better"):
        raise ModelError(
            "The learned model did not beat the hand-set one on held-out people: "
            + " ".join(report.get("reasons") or [])
        )
    model = Model.from_json(report["candidate"])
    write_setting(engine, MODEL_SETTING, model.to_json(), actor)
    _switch_off_auto_accept(engine, actor, "a new model was released")
    return model


def revert(engine: Engine, actor: str) -> None:
    """Go back to the hand-set model (and switch auto-accept off)."""
    write_setting(engine, MODEL_SETTING, None, actor)
    _switch_off_auto_accept(engine, actor, "the model was reverted")


def _switch_off_auto_accept(engine: Engine, actor: str, why: str) -> None:
    from tessera.evidence.auto_accept import SETTING

    current = read_setting(engine, SETTING)
    if current and current.get("enabled"):
        write_setting(
            engine, SETTING, {**current, "enabled": False, "disabled_because": why}, actor
        )


__all__ = [
    "AUTO_ACTOR",
    "Example",
    "Model",
    "ModelError",
    "active_model",
    "evaluate",
    "evaluate_examples",
    "examples_from_ledger",
    "fit",
    "predict",
    "release",
    "revert",
]
