"""Recalculate every saved score and summary from raw fields, with no model calls.

  uv run python -m reliability.audit

Fails if any stored value disagrees with a fresh calculation, or the report does not match.
"""

import argparse
from pathlib import Path

from evals.__main__ import render
from evals.scoring import RunScore, summarize
from investigator.tools import load_cases
from reliability.analysis import RESULTS, load_scores


def audit_run(score: RunScore, truth) -> list[str]:
    """Each check recomputes a stored value from the fields it was derived from."""
    problems = []
    concluded = score.outcome == "conclusive"
    right = (score.top_category, score.top_component) == (truth.category, truth.component)
    expected = {
        "correct": (not concluded) if truth.outcome == "inconclusive" else concluded and right,
        "expected_outcome": truth.outcome,
        "wrong_conclusion": concluded and not (truth.outcome == "conclusive" and right),
        "missed_conclusion": truth.outcome == "conclusive" and not concluded,
        "fell_for_trap": bool(
            concluded and truth.trap_component and score.top_component == truth.trap_component
        ),
        "total_tokens": score.input_tokens + score.output_tokens,
    }
    if not score.valid:
        return []
    for field, value in expected.items():
        if getattr(score, field) != value:
            problems.append(f"{field} is {getattr(score, field)!r}, recalculated {value!r}")
    if score.thinking_tokens > score.output_tokens:
        problems.append("thinking tokens exceed output tokens")
    for field in ("citation_validity", "evidence_precision", "evidence_recall"):
        value = getattr(score, field)
        if value is not None and not 0 <= value <= 1:
            problems.append(f"{field} {value} is outside 0 to 1")
    if score.cited or score.verified_supporting:
        cited = set(score.cited) | set(score.verified_supporting)
        key = set(truth.key_evidence)
        if key:
            hits = len(cited & key)
            if score.evidence_recall != hits / len(key):
                problems.append("evidence_recall does not match its rows")
            if score.evidence_precision != (hits / len(cited) if cited else 0.0):
                problems.append("evidence_precision does not match its rows")
    return problems


def audit(scores: list[RunScore], report_text: str | None, model: str) -> dict:
    truths = {case.incident.id: case.truth for case in load_cases()}
    problems = []
    for score in scores:
        for problem in audit_run(score, truths[score.incident_id]):
            problems.append(f"{score.incident_id} {score.system} #{score.repeat}: {problem}")

    for system in {s.system for s in scores}:
        group = [s for s in scores if s.system == system and s.valid]
        summary = summarize([s for s in scores if s.system == system])
        recount = {
            "accuracy": sum(s.correct for s in group) / len(group),
            "total_tokens": sum(s.total_tokens for s in group) / len(group),
            "tool_calls": sum(s.tool_calls for s in group) / len(group),
            "latency_s": sum(s.latency_s for s in group) / len(group),
        }
        for key, value in recount.items():
            if abs(summary[key] - value) > 1e-9:
                problems.append(f"{system}: summary {key} {summary[key]} != recount {value}")

    if report_text is not None and render(scores, model) != report_text:
        problems.append("results.md does not match a fresh render of results.json")
    auditable = sum(1 for s in scores if s.cited or s.verified_supporting)
    return {"runs": len(scores), "problems": problems, "evidence_auditable": auditable}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=RESULTS / "results.json")
    parser.add_argument("--report", type=Path, default=RESULTS / "results.md")
    parser.add_argument("--model", default="gemini:gemini-3.6-flash")
    args = parser.parse_args()
    result = audit(load_scores(args.results), args.report.read_text(), args.model)
    print(f"Audited {result['runs']} runs.")
    print(
        f"Evidence precision and recall rechecked row by row in {result['evidence_auditable']} "
        "runs. Older runs did not save their cited rows."
    )
    for problem in result["problems"]:
        print(f"FAILED  {problem}")
    if result["problems"]:
        parser.exit(1, f"Audit failed: {len(result['problems'])} problems.\n")
    print("Audit passed: every stored value matches a fresh calculation.")


if __name__ == "__main__":
    main()
