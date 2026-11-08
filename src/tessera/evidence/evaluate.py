"""How well does authorship matching work on this organization's own data?

The gold set is every human decision on an ``authored`` suggestion (accepted = yes,
rejected = no), plus an optional CSV of adjudicated pairs. Reports precision and recall at
each threshold, candidate retrieval recall, calibration, review burden, and the same
measures for slices of the data (common versus rare names, with versus without ORCID).
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine

from tessera.config import Config
from tessera.core.clock import isoformat, utc_now
from tessera.core.errors import TesseraError
from tessera.evidence import store
from tessera.evidence.authorship import METHOD

COMMON_NAME = 5  # people sharing a name before it counts as "common"


@dataclass
class Example:
    person_id: str
    pmid: str
    label: bool
    score: float | None  # None: never retrieved as a candidate
    features: set[str]
    ambiguity: int


def load_gold_csv(engine: Engine, path: Path) -> dict[tuple[str, str], bool]:
    """CSV columns: person (an internal ID, ORCID, NIH profile ID, or email), pmid, label."""
    try:
        rows = list(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()))
    except OSError as exc:
        raise TesseraError(f"Cannot read {path}: {exc}") from exc
    people = store.load_people(engine, ("active", "former", "candidate"))
    lookup = {p.id: p.id for p in people}
    for p in people:
        for values in p.identifiers.values():
            for value in values:
                lookup[value.lower()] = p.id
    gold = {}
    for row in rows:
        who = (row.get("person") or "").strip()
        person_id = lookup.get(who) or lookup.get(who.lower())
        pmid = (row.get("pmid") or "").strip()
        label = (row.get("label") or "").strip().lower()
        if person_id and pmid.isdigit() and label in {"1", "0", "yes", "no", "true", "false"}:
            gold[(person_id, pmid)] = label in {"1", "yes", "true"}
    return gold


def gather(engine: Engine, gold_csv: Path | None = None) -> list[Example]:
    rows = store.assertions_for(engine, predicate="authored", reported_only=False)
    by_pair = {(r["subject_id"], r["object_id"].removeprefix("pub:")): r for r in rows}
    gold: dict[tuple[str, str], bool] = {}
    for pair, row in by_pair.items():
        if row["locked"] and row["status"] in {"accepted", "rejected"}:
            gold[pair] = row["status"] == "accepted"
    if gold_csv:
        gold.update(load_gold_csv(engine, gold_csv))
    examples = []
    for pair, label in gold.items():
        row = by_pair.get(pair)
        current = row is not None and row["reported"] and row["method"].startswith("authorship/")
        features = {e["feature"] for e in (row["evidence"] if row else [])}
        ambiguity = next(
            (
                int(e["value"])
                for e in (row["evidence"] if row else [])
                if e["feature"] == "name_ambiguity"
            ),
            1,
        )
        examples.append(
            Example(pair[0], pair[1], label, row["score"] if current else None, features, ambiguity)
        )
    return examples


def _metrics(examples: list[Example], threshold: float) -> dict[str, Any]:
    positives = [e for e in examples if e.label]
    predicted = [e for e in examples if e.score is not None and e.score >= threshold]
    true_pos = sum(1 for e in predicted if e.label)
    return {
        "threshold": threshold,
        "predicted": len(predicted),
        "precision": round(true_pos / len(predicted), 4) if predicted else None,
        "recall": round(true_pos / len(positives), 4) if positives else None,
    }


def evaluate(engine: Engine, config: Config, gold_csv: Path | None = None) -> dict[str, Any]:
    examples = gather(engine, gold_csv)
    positives = [e for e in examples if e.label]
    retrieved = [e for e in positives if e.score is not None]
    calibration = []
    for low in (0.0, 0.2, 0.4, 0.6, 0.8, 0.95):
        high = {0.0: 0.2, 0.2: 0.4, 0.4: 0.6, 0.6: 0.8, 0.8: 0.95, 0.95: 1.01}[low]
        bucket = [e for e in examples if e.score is not None and low <= e.score < high]
        if bucket:
            calibration.append(
                {
                    "range": [low, min(high, 1.0)],
                    "examples": len(bucket),
                    "mean_score": round(sum(e.score or 0 for e in bucket) / len(bucket), 4),
                    "fraction_correct": round(sum(e.label for e in bucket) / len(bucket), 4),
                }
            )
    open_suggestions = [
        r
        for r in store.assertions_for(engine, predicate="authored", statuses=("suggested",))
        if (r["score"] or 0) >= config.evidence.review_threshold
    ]
    slices: dict[str, list[Example]] = defaultdict(list)
    for e in examples:
        slices["common names" if e.ambiguity >= COMMON_NAME else "rare names"].append(e)
        slices["with ORCID match" if "orcid_match" in e.features else "without ORCID match"].append(
            e
        )
    thresholds = {
        "strong": config.evidence.strong_threshold,
        "review": config.evidence.review_threshold,
    }
    report = {
        "method": METHOD,
        "generated_at": isoformat(utc_now()),
        "gold": {
            "examples": len(examples),
            "positives": len(positives),
            "negatives": len(examples) - len(positives),
            "from_csv": str(gold_csv) if gold_csv else None,
        },
        "retrieval_recall": round(len(retrieved) / len(positives), 4) if positives else None,
        "at": {name: _metrics(examples, t) for name, t in thresholds.items()},
        "calibration": calibration,
        "review_burden": {
            "open_suggestions_above_review": len(open_suggestions),
        },
        "slices": {
            name: {
                "examples": len(group),
                **{band: _metrics(group, t) for band, t in thresholds.items()},
            }
            for name, group in sorted(slices.items())
        },
        "targets": {"strong_precision": 0.99, "retrieval_recall": 0.95, "review_precision": 0.90},
        "caveats": [
            "Gold labels come from curation decisions, which were made while viewing the "
            "suggestions; add an independently adjudicated sample with --gold for an "
            "unbiased estimate.",
            "Evidence from a person's other papers is computed leave-one-out, so a decision "
            "never counts as evidence for itself.",
        ],
    }
    return report


def write_report(report: dict[str, Any], directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = report["generated_at"].replace(":", "").replace("-", "")[:15]
    json_path = directory / f"authorship-{stamp}.json"
    md_path = directory / f"authorship-{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def render_markdown(report: dict[str, Any]) -> str:
    gold = report["gold"]
    lines = [
        f"# Authorship evaluation ({report['method']})",
        "",
        f"Generated {report['generated_at']}. Gold set: {gold['examples']} decisions "
        f"({gold['positives']} authored, {gold['negatives']} not).",
        "",
        "| Measure | Value | Target |",
        "|---|---|---|",
        f"| Candidate retrieval recall | {_pct(report['retrieval_recall'])} | 95% |",
        f"| Precision at the strong threshold ({report['at']['strong']['threshold']}) | "
        f"{_pct(report['at']['strong']['precision'])} | 99% |",
        f"| Recall at the strong threshold | {_pct(report['at']['strong']['recall'])} | |",
        f"| Precision at the review threshold ({report['at']['review']['threshold']}) | "
        f"{_pct(report['at']['review']['precision'])} | 90% |",
        f"| Recall at the review threshold | {_pct(report['at']['review']['recall'])} | |",
        f"| Open suggestions above the review threshold | "
        f"{report['review_burden']['open_suggestions_above_review']} | |",
        "",
        "## Calibration",
        "",
        "| Score range | Examples | Mean score | Fraction correct |",
        "|---|---|---|---|",
        *[
            f"| {c['range'][0]:.2f}-{c['range'][1]:.2f} | {c['examples']} | "
            f"{c['mean_score']:.2f} | {c['fraction_correct']:.2f} |"
            for c in report["calibration"]
        ],
        "",
        "## Slices",
        "",
        "| Slice | Examples | Strong precision | Review precision | Review recall |",
        "|---|---|---|---|---|",
        *[
            f"| {name} | {s['examples']} | {_pct(s['strong']['precision'])} | "
            f"{_pct(s['review']['precision'])} | {_pct(s['review']['recall'])} |"
            for name, s in report["slices"].items()
        ],
        "",
        "## Caveats",
        "",
        *[f"- {c}" for c in report["caveats"]],
        "",
    ]
    return "\n".join(lines)
