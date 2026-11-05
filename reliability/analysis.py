"""Calibration and cost from saved results, with no model calls.

uv run python -m reliability.analysis
uv run python -m reliability.analysis --input-price 0.10 --output-price 0.40
"""

import argparse
import json
from collections.abc import Sequence
from math import sqrt
from pathlib import Path

from pydantic import TypeAdapter

from evals.scoring import RunScore

RESULTS = Path(__file__).parent.parent / "evals" / "results"
LABELS = ("high", "medium", "low")


def load_scores(path: Path) -> list[RunScore]:
    return TypeAdapter(list[RunScore]).validate_python(json.loads(path.read_text()))


def wilson_interval(correct: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """A 95% range for a success rate, which stays honest on small samples."""
    if not total:
        return 0.0, 1.0
    rate = correct / total
    denominator = 1 + z**2 / total
    centre = (rate + z**2 / (2 * total)) / denominator
    margin = z * sqrt(rate * (1 - rate) / total + z**2 / (4 * total**2)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def calibration(scores: Sequence[RunScore]) -> dict[str, tuple[int, int]]:
    """Conclusions by confidence label, as (correct, total). Do labels predict being right?"""
    table = {}
    concluded = [s for s in scores if s.valid and s.outcome == "conclusive"]
    for label in LABELS:
        group = [s for s in concluded if s.top_confidence == label]
        table[label] = (sum(s.correct for s in group), len(group))
    return table


def calibration_finding(table: dict[str, tuple[int, int]]) -> str:
    high, medium = table["high"], table["medium"]
    if not high[1] or not medium[1]:
        return "Not enough conclusions at each level to compare."
    high_rate, medium_rate = high[0] / high[1], medium[0] / medium[1]
    if high_rate == medium_rate:
        return "Both labels were right equally often, so these results cannot separate them."
    if high_rate > medium_rate:
        return "High-confidence conclusions were right more often than medium."
    return "High-confidence conclusions were right less often than medium."


def cost_per_run(scores: Sequence[RunScore], input_price: float, output_price: float) -> float:
    """Mean cost from prices per million tokens. Prices change, so the caller supplies them."""
    valid = [s for s in scores if s.valid]
    if not valid:
        return 0.0
    total = sum(s.input_tokens * input_price + s.output_tokens * output_price for s in valid)
    return total / len(valid) / 1_000_000


def tokens_by_agent(scores: Sequence[RunScore]) -> dict[str, float]:
    valid = [s for s in scores if s.valid and s.tokens_by_agent]
    agents = {agent for s in valid for agent in s.tokens_by_agent}
    usage = {a: sum(s.tokens_by_agent.get(a, 0) for s in valid) / len(valid) for a in agents}
    return dict(sorted(usage.items(), key=lambda item: -item[1]))


def render(scores: Sequence[RunScore], input_price=None, output_price=None) -> str:
    systems = sorted({s.system for s in scores}, reverse=True)
    lines = [
        "# Reliability analysis\n",
        f"Calculated from {len(scores)} saved runs, with no new model calls.\n",
        "## Calibration\n",
        "Conclusions at each confidence label, and how many were correct. The range is a 95% "
        "Wilson interval. Wide ranges mean the sample is too small to be sure.\n",
        "| System | Confidence | Correct | Rate | 95% range |",
        "|---|---|---|---|---|",
    ]
    findings = []
    for system in systems:
        table = calibration([s for s in scores if s.system == system])
        for label in LABELS:
            correct, total = table[label]
            if total:
                low, high = wilson_interval(correct, total)
                lines.append(
                    f"| {system} | {label} | {correct}/{total} | {correct / total:.0%} "
                    f"| {low:.0%} to {high:.0%} |"
                )
            else:
                lines.append(f"| {system} | {label} | 0/0 | n/a | n/a |")
        findings.append(f"- **{system}:** {calibration_finding(table)}")
    lines += ["", *findings]
    lines += [
        "",
        "Low-confidence hypotheses never conclude, by rule, so they do not appear as conclusions.",
    ]

    lines += ["\n## Tokens by system\n", "| System | Input per run | Output per run | Thinking |"]
    lines.append("|---|---|---|---|")
    for system in systems:
        group = [s for s in scores if s.system == system and s.valid]
        size = max(len(group), 1)
        lines.append(
            f"| {system} | {sum(s.input_tokens for s in group) / size:,.0f} "
            f"| {sum(s.output_tokens for s in group) / size:,.0f} "
            f"| {sum(s.thinking_tokens for s in group) / size:,.0f} |"
        )

    if input_price is not None and output_price is not None:
        lines += ["\n## Estimated cost\n", "Using the prices given to this command.\n"]
        lines += ["| System | Per run | Per 1,000 runs |", "|---|---|---|"]
        for system in systems:
            cost = cost_per_run(
                [s for s in scores if s.system == system], input_price, output_price
            )
            lines.append(f"| {system} | ${cost:.4f} | ${cost * 1000:,.2f} |")

    lines.append("\n## Tokens by agent\n")
    usage = {
        system: tokens_by_agent([s for s in scores if s.system == system]) for system in systems
    }
    if any(usage.values()):
        lines += ["| System | Agent | Tokens per run |", "|---|---|---|"]
        for system in systems:
            lines += [f"| {system} | {a} | {t:,.0f} |" for a, t in usage[system].items()]
    else:
        lines.append(
            "Not in these results. The evaluation now records it, so it appears from the next run."
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=RESULTS / "results.json")
    parser.add_argument("--out", type=Path, default=RESULTS / "analysis.md")
    parser.add_argument("--input-price", type=float, help="price per million input tokens")
    parser.add_argument("--output-price", type=float, help="price per million output tokens")
    args = parser.parse_args()
    text = render(load_scores(args.results), args.input_price, args.output_price)
    args.out.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
