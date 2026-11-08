"""Learned weights (authorship/v2) and auto-accept (CUR-05, CUR-10)."""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine

from tessera.config import Config, example_config_text, load_config
from tessera.evidence import auto_accept, store
from tessera.evidence.authorship import METHOD
from tessera.evidence.model import (
    EVALUATION_SETTING,
    Example,
    Model,
    ModelError,
    active_model,
    evaluate,
    evaluate_examples,
    examples_from_ledger,
    fit,
    release,
    revert,
    write_setting,
)
from tessera.evidence.people import import_roster
from tessera.evidence.resolve import resolve
from tessera.home import DataHome
from tessera.ledger import migrate
from tessera.ledger.db import make_engine

from . import synthetic_org

TODAY = date(2026, 11, 7)

# --- learning from generated decisions --------------------------------------------------------

HAND_SET = {
    "org_affiliation": 2.5,
    "coauthor_overlap": 2.0,
    "grant_link": 3.0,
    "name_ambiguity": -1.0,
}
TRUE_MULTIPLIERS = {
    "org_affiliation": 0.4,
    "coauthor_overlap": 2.0,
    "grant_link": 1.0,
    "name_ambiguity": 1.5,
}


def generated(n: int, seed: int = 3) -> list[Example]:
    """Decisions made by a world where affiliations count for less and coauthors for more
    than the hand-set weights assume."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        x = {"prior": -1.5}
        for feature, weight in HAND_SET.items():
            if rng.random() < 0.5:
                x[feature] = weight
        z = sum(v * TRUE_MULTIPLIERS.get(f, 1.0) for f, v in x.items())
        label = rng.random() < 1 / (1 + math.exp(-z))
        out.append(Example(f"asr_{i}", f"per_{i % 60}", label, x))
    return out


@pytest.fixture
def config(config_file: Path) -> Config:
    return load_config(config_file, env={})


def test_fitting_recovers_the_weights_a_world_uses() -> None:
    model = fit(generated(6000))
    for feature, truth in TRUE_MULTIPLIERS.items():
        assert model.multipliers[feature] == pytest.approx(truth, abs=0.25), feature
    assert abs(model.offset) < 0.4


def test_few_decisions_barely_move_the_hand_set_weights() -> None:
    model = fit(generated(30))
    assert all(abs(m - 1.0) < 0.25 for m in model.multipliers.values()), model.multipliers


def test_a_better_model_is_found_on_held_out_people(config: Config) -> None:
    report = evaluate_examples(generated(3000), config)
    assert report["better"], report["reasons"]
    assert report["fitted"]["log_loss"] < report["base"]["log_loss"]
    assert report["candidate"]["method"].startswith("authorship/v2-")


def test_a_model_that_is_not_better_is_not_offered(config: Config) -> None:
    # Decisions made exactly as the hand-set model predicts: learning cannot beat it.
    rng = random.Random(5)
    examples = []
    for i in range(3000):
        x = {"prior": -1.5, **{f: w for f, w in HAND_SET.items() if rng.random() < 0.5}}
        label = rng.random() < 1 / (1 + math.exp(-sum(x.values())))
        examples.append(Example(f"asr_{i}", f"per_{i % 60}", label, x))
    report = evaluate_examples(examples, config)
    assert not report["better"] and "does not predict decisions better" in " ".join(
        report["reasons"]
    )


def test_too_few_decisions_are_reported(config: Config) -> None:
    report = evaluate_examples(generated(50), config)
    assert not report["better"] and report["candidate"] is None
    assert any("fitting needs 200" in r for r in report["reasons"])


def test_applying_a_model_keeps_explanations_honest() -> None:
    model = Model(method="authorship/v2-test", multipliers={"coauthor_overlap": 2.0}, offset=-0.5)
    features = [
        {"feature": "prior", "weight": -1.5},
        {"feature": "coauthor_overlap", "weight": 2.0},
        {"feature": "org_affiliation", "weight": 2.5},
    ]
    total = model.apply(features)
    assert total == pytest.approx(-1.5 + 4.0 + 2.5 - 0.5)
    by_name = {f["feature"]: f for f in features}
    assert (by_name["coauthor_overlap"]["weight"], by_name["coauthor_overlap"]["base_weight"]) == (
        4.0,
        2.0,
    )
    assert by_name["model_offset"]["weight"] == -0.5  # the learned offset is shown, too


# --- on the synthetic organization ----------------------------------------------------------


@dataclass
class Org:
    config: Config
    home: DataHome
    engine: Engine
    decided: int


@pytest.fixture
def org(tmp_path: Path) -> Iterator[Org]:
    (tmp_path / "tessera.toml").write_text(example_config_text())
    config = load_config(tmp_path / "tessera.toml", env={})
    home = DataHome(config.data.home)
    home.create()
    engine = make_engine(config.ledger_url)
    migrate.upgrade(engine)
    world = synthetic_org.build(tmp_path)
    synthetic_org.load(world, home.lake)
    import_roster(engine, world.roster_path)
    resolve(config, home, engine, today=TODAY)
    people = store.load_people(engine)
    key_of = {}
    for r in world.insiders:
        [match] = [p for p in people if any(n.family == r.family for n in p.names)]
        key_of[match.id] = r.key
    decided = 0
    # A curator works through seven people in ten, accepting what is true.
    for row in store.assertions_for(engine, predicate="authored", statuses=("suggested",)):
        person = row["subject_id"]
        if person not in key_of or int(hashlib.sha256(person.encode()).hexdigest(), 16) % 10 >= 7:
            continue
        truth = (key_of[person], row["object_id"].removeprefix("pub:")) in world.truth
        store.decide(engine, row["id"], "accept" if truth else "reject", "Curator")
        decided += 1
    resolve(config, home, engine, today=TODAY)
    yield Org(config, home, engine, decided)
    engine.dispose()


def test_decisions_are_the_training_data(org: Org) -> None:
    examples = examples_from_ledger(org.engine)
    assert len(examples) == org.decided
    report = evaluate(org.engine, org.config)
    # The synthetic organization is nearly separable; there are too few rejections to learn from.
    assert not report["better"] and report["base"]["strong"]["precision"] == 1.0


def test_releasing_and_reverting_a_learned_model(org: Org) -> None:
    with pytest.raises(ModelError, match="no learned model"):  # too few rejections to fit
        release(org.engine, "Admin")
    learned = Model(method="authorship/v2-test", multipliers={"coauthor_overlap": 1.5}, offset=0.2)
    write_setting(org.engine, "authorship_model", learned.to_json(), "test")
    resolve(org.config, org.home, org.engine, today=TODAY)
    rows = store.assertions_for(org.engine, predicate="authored", statuses=("suggested",))
    assert rows and {r["method"] for r in rows} == {"authorship/v2-test"}
    coauthors = next(f for r in rows for f in r["evidence"] if f["feature"] == "coauthor_overlap")
    assert coauthors["weight"] == pytest.approx(coauthors["base_weight"] * 1.5, abs=1e-3)
    revert(org.engine, "Admin")
    resolve(org.config, org.home, org.engine, today=TODAY)
    assert active_model(org.engine).method == METHOD
    rows = store.assertions_for(org.engine, predicate="authored", statuses=("suggested",))
    assert {r["method"] for r in rows} == {METHOD}


def test_auto_accept_is_gated_runs_in_batches_and_undoes(org: Org) -> None:
    engine = org.engine
    with pytest.raises(auto_accept.AutoAcceptError, match="Evaluate the model first"):
        auto_accept.enable(engine, 0.99, "Admin")
    evaluate(engine, org.config)
    with pytest.raises(auto_accept.AutoAcceptError, match="Choose a threshold"):
        auto_accept.enable(engine, 0.5, "Admin")
    state = auto_accept.enable(engine, 0.99, "Admin")
    assert state["enabled"] and state["precision"] >= 0.99

    open_before = store.assertions_for(engine, predicate="authored", statuses=("suggested",))
    result = resolve(org.config, org.home, engine, today=TODAY).counts["auto_accept"]
    assert result["accepted"] > 0
    accepted = [
        r
        for r in store.assertions_for(engine, predicate="authored", statuses=("accepted",))
        if r["decided_by"] == auto_accept.AUTO_ACTOR
    ]
    assert len(accepted) == result["accepted"]
    assert all(r["score"] >= 0.99 for r in accepted)
    # Automatic acceptances never become training data.
    assert len(examples_from_ledger(engine)) == org.decided
    [batch] = auto_accept.batches(engine)
    assert batch["accepted"] == result["accepted"]

    restored = auto_accept.undo_batch(engine, batch["id"], "Admin")
    assert restored == result["accepted"]
    assert not auto_accept.status(engine)["enabled"]  # so the next sync does not redo it
    open_after = store.assertions_for(engine, predicate="authored", statuses=("suggested",))
    assert len(open_after) == len(open_before)
    with pytest.raises(auto_accept.AutoAcceptError, match="already been undone"):
        auto_accept.undo_batch(engine, batch["id"], "Admin")


def test_auto_accept_refuses_a_threshold_below_the_bar(org: Org) -> None:
    evaluate(org.engine, org.config)
    report = dict(read_report(org.engine))
    report["base"] = {
        **report["base"],
        "curve": [{"threshold": 0.95, "support": 400, "precision": 0.985, "recall": 0.9}],
    }
    write_setting(org.engine, EVALUATION_SETTING, report, "test")
    with pytest.raises(auto_accept.AutoAcceptError, match="below the 99% required"):
        auto_accept.enable(org.engine, 0.95, "Admin")
    report["base"]["curve"] = [{"threshold": 0.95, "support": 12, "precision": 1.0, "recall": 0.1}]
    write_setting(org.engine, EVALUATION_SETTING, report, "test")
    with pytest.raises(auto_accept.AutoAcceptError, match="at least 50"):
        auto_accept.enable(org.engine, 0.95, "Admin")


def test_a_new_model_switches_auto_accept_off(org: Org) -> None:
    evaluate(org.engine, org.config)
    auto_accept.enable(org.engine, 0.99, "Admin")
    learned = Model(method="authorship/v2-test", multipliers={"grant_link": 1.2})
    write_setting(org.engine, "authorship_model", learned.to_json(), "test")
    result = resolve(org.config, org.home, org.engine, today=TODAY).counts["auto_accept"]
    assert result == {"enabled": False, "accepted": 0, "stopped": "the model changed"}
    assert not auto_accept.status(org.engine)["enabled"]


def read_report(engine: Engine) -> dict:
    from tessera.evidence.model import read_setting

    report = read_setting(engine, EVALUATION_SETTING)
    assert report is not None
    return report


def test_a_model_that_wins_is_released_and_auto_accept_uses_its_measures(org: Org) -> None:
    report = evaluate_examples(generated(3000), org.config)
    write_setting(org.engine, EVALUATION_SETTING, report, "test")
    model = release(org.engine, "Admin")
    assert active_model(org.engine).method == model.method == report["candidate"]["method"]
    fitted_curve = {p["threshold"]: p for p in report["fitted"]["curve"]}
    try:
        state = auto_accept.enable(org.engine, 0.99, "Admin")
    except auto_accept.AutoAcceptError as exc:  # the generated world may not reach 99% there
        assert (fitted_curve[0.99]["precision"] or 0) < 0.99 or fitted_curve[0.99][
            "support"
        ] < 50, exc
    else:
        assert state["method"] == model.method
