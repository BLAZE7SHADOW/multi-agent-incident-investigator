"""Offline eval of the evidence rules alone, scored with precision and recall.

  uv run python -m reliability.rule_eval
  uv run python reliability/rule_eval.py --json before.json    (run with an older investigator/)
  uv run python -m reliability.rule_eval --before before.json

Labels are those a perfect Verifier would give, so this isolates the rules from LLM mistakes.
Imports only `investigator`, so the same file can score older versions of the rules.
"""

import argparse
import json
from pathlib import Path

import yaml

from investigator.evidence import decide_outcome, rank_hypotheses
from investigator.schemas import (
    Critique,
    Evidence,
    Hypothesis,
    HypothesisVerdict,
    RowVerdict,
    Verification,
)

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "data" / "rule_eval.yaml"
TABLES = {"logs": "logs", "metrics": "metrics", "deployments": "deployments", "history": "history"}


def _ledger(incident_id: str, metric_values: dict) -> dict[str, Evidence]:
    data = yaml.safe_load((ROOT / "data" / "incidents.yaml").read_text())
    case = next(c for c in data["cases"] if c["incident"]["id"] == incident_id)
    ledger = {}
    for table, source in TABLES.items():
        for row in case["world"].get(table, []):
            if row["id"] in metric_values:
                row = {**row, "values": metric_values[row["id"]]}
            ledger[row["id"]] = Evidence(id=row["id"], source=source, content=json.dumps(row))
    for row in data["topology"]:
        ledger[row["id"]] = Evidence(id=row["id"], source="dependencies", content=json.dumps(row))
    return ledger


def run_scenario(scenario: dict) -> dict:
    ledger = _ledger(scenario["incident"], scenario.get("metric_values", {}))
    hypotheses, verdicts = [], []
    for spec in scenario["hypotheses"]:
        supports, contradicts = spec.get("supports", []), spec.get("contradicts", [])
        hypotheses.append(
            Hypothesis(
                id=spec["id"],
                statement=spec["id"],
                category=spec["category"],
                component=spec["component"],
                supporting=supports,
                contradicting=contradicts,
                assumptions=[],
            )
        )
        rows = [RowVerdict(evidence_id=r, label="supports", reason="") for r in supports]
        rows += [RowVerdict(evidence_id=r, label="contradicts", reason="") for r in contradicts]
        verdicts.append(HypothesisVerdict(hypothesis_id=spec["id"], rows=rows))
    ranked = rank_hypotheses(hypotheses, Verification(verdicts=verdicts), ledger, [])
    critique = Critique(verdict=scenario.get("critic", "accept"), objections=[], requests=[])
    outcome, reasons = decide_outcome(ranked, critique)
    top = ranked[0]
    expected = scenario["expected"]
    if expected["outcome"] == "conclusive":
        right = (top.category, top.component) == (expected["category"], expected["component"])
        correct = outcome == "conclusive" and right
    else:
        correct = outcome == "inconclusive"
    return {
        "name": scenario["name"],
        "origin": scenario["origin"],
        "known_gap": scenario.get("known_gap", False),
        "expected": expected,
        "outcome": outcome,
        "top": f"{top.category}/{top.component}",
        "net": [h.net_support for h in ranked],
        "reasons": reasons,
        "correct": correct,
    }


def confusion(results: list[dict]) -> dict:
    """The positive class is "concluded the right cause". A wrong conclusion is a false positive."""
    tp = fp = fn = tn = 0
    for r in results:
        should = r["expected"]["outcome"] == "conclusive"
        concluded = r["outcome"] == "conclusive"
        if concluded and r["correct"]:
            tp += 1
        elif concluded:
            fp += 1
            fn += should  # a wrong cause also misses the right one
        elif should:
            fn += 1
        else:
            tn += 1
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "accuracy": sum(r["correct"] for r in results) / len(results),
        "wrong_conclusions": fp,
    }


def evaluate(path: Path = SCENARIOS) -> list[dict]:
    return [run_scenario(s) for s in yaml.safe_load(path.read_text())["scenarios"]]


def _fmt(value) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def render(after: list[dict], before: list[dict] | None = None) -> str:
    columns = [("Before", before), ("After", after)] if before else [("Result", after)]
    lines = [
        "# Evidence rule evaluation\n",
        "Scores the evidence rules alone on scenarios from `data/rule_eval.yaml`, with no model "
        "calls. Labels are what a perfect Verifier would give, so this shows what the rules "
        "decide, not what the full system would do with a real model.\n",
        "| Metric | " + " | ".join(name for name, _ in columns) + " |",
        "|---|" + "---|" * len(columns),
    ]
    stats = [confusion(results) for _, results in columns]
    for label, key, fmt in [
        ("Scenarios correct", "accuracy", _fmt),
        ("Precision", "precision", _fmt),
        ("Recall", "recall", _fmt),
        ("Wrong conclusions", "wrong_conclusions", str),
        ("True positives (right cause concluded)", "tp", str),
        ("False positives (wrong conclusion)", "fp", str),
        ("False negatives (right cause missed)", "fn", str),
        ("True negatives (rightly inconclusive)", "tn", str),
    ]:
        lines.append(f"| {label} | " + " | ".join(fmt(s[key]) for s in stats) + " |")
    lines += [
        "",
        "Precision = TP / (TP + FP). Recall = TP / (TP + FN). A wrong conclusion counts as both a "
        "false positive and a false negative, because it also misses the right cause.\n",
        "## Scenarios\n",
        "| Scenario | Origin | " + " | ".join(name for name, _ in columns) + " |",
        "|---|---|" + "---|" * len(columns),
    ]
    for index, result in enumerate(after):
        cells = []
        for _, results in columns:
            r = results[index]
            mark = "✓" if r["correct"] else "✗"
            cells.append(f"{mark} {r['outcome']}, {r['top']}, net {r['net']}")
        gap = " (known gap)" if result["known_gap"] else ""
        lines.append(f"| {result['name']}{gap} | {result['origin']} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, help="write raw results, e.g. from older rules")
    parser.add_argument("--before", type=Path, help="raw results to compare against")
    parser.add_argument("--out", type=Path, default=ROOT / "evals" / "results" / "rule_eval.md")
    args = parser.parse_args()
    results = evaluate()
    if args.json:
        args.json.write_text(json.dumps(results, indent=1))
        return
    before = json.loads(args.before.read_text()) if args.before else None
    text = render(results, before)
    args.out.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
