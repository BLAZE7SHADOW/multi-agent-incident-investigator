"""Fail if a change made results worse. Compares saved results, with no model calls.

  uv run python -m reliability.regression                 compare results to the baseline
  uv run python -m reliability.regression --update        accept results as the new baseline

Use after any model, prompt or code change, once `python -m evals` has produced new results.
"""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from statistics import mean

from evals.scoring import RunScore, summarize
from reliability.analysis import RESULTS, load_scores

BASELINE = RESULTS / "baseline.json"

# metric, kind, allowed change. Limits are wide because repeated LLM runs vary.
CHECKS = (
    ("accuracy", "drop", 0.05),
    ("wrong_conclusion_rate", "rise", 0.0),
    ("trap_rate", "rise", 0.0),
    ("citation_validity", "drop", 0.02),
    ("evidence_recall", "drop", 0.05),
    ("total_tokens", "rise_ratio", 0.20),
    ("latency_s", "rise_ratio", 0.30),
)


def snapshot(scores: Sequence[RunScore]) -> dict:
    """What later runs are compared against."""
    result = {}
    for system in sorted({score.system for score in scores}):
        group = [score for score in scores if score.system == system]
        incidents = {}
        for incident in sorted({score.incident_id for score in group}):
            runs = [s for s in group if s.incident_id == incident and s.valid]
            incidents[incident] = mean(s.correct for s in runs) if runs else None
        result[system] = {"summary": summarize(group), "incidents": incidents}
    return result


def compare(baseline: dict, current: dict) -> tuple[list[str], list[str]]:
    """Returns failures, which block a change, and warnings, which need a look."""
    failures: list[str] = []
    warnings: list[str] = []
    for system, before in baseline.items():
        after = current.get(system)
        if after is None:
            failures.append(f"{system}: missing from current results")
            continue
        if after["summary"].get("invalid_runs"):
            failures.append(f"{system}: {after['summary']['invalid_runs']} invalid runs")
        for metric, kind, allowed in CHECKS:
            old, new = before["summary"].get(metric), after["summary"].get(metric)
            if old is None or new is None:
                continue
            if kind == "drop":
                failed = round(old - new, 6) > allowed
            elif kind == "rise":
                failed = round(new - old, 6) > allowed
            else:
                failed = new > old * (1 + allowed)
            if failed:
                failures.append(f"{system}: {metric} went from {old:,.3f} to {new:,.3f}")
        for incident, old in before["incidents"].items():
            if incident not in after["incidents"]:
                failures.append(f"{system}: {incident} was not run")
            else:
                new = after["incidents"][incident]
                if old is not None and new is not None and new < old:
                    warnings.append(f"{system}: {incident} accuracy fell, {old:.0%} to {new:.0%}")
    return failures, warnings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=RESULTS / "results.json")
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--update", action="store_true", help="accept results as the new baseline")
    args = parser.parse_args()

    current = snapshot(load_scores(args.results))
    if args.update:
        args.baseline.write_text(json.dumps(current, indent=1))
        print(f"Baseline updated: {args.baseline}")
        return
    if not args.baseline.exists():
        parser.exit(2, f"No baseline at {args.baseline}. Create one with --update.\n")
    failures, warnings = compare(json.loads(args.baseline.read_text()), current)
    for warning in warnings:
        print(f"WARNING  {warning}")
    for failure in failures:
        print(f"FAILED   {failure}")
    if failures:
        parser.exit(1, f"Regression check failed: {len(failures)} problems.\n")
    print("Regression check passed.")


if __name__ == "__main__":
    main()
