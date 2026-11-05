"""Compare both systems on the test incidents: uv run python -m evals --repeats 3"""

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

from evals.scoring import RunScore, score_run, summarize
from investigator.schemas import Case
from investigator.tools import load_cases
from reliability.golden import load_golden
from reliability.runtime import run_baseline, run_incident

SYSTEMS = {"multi_agent": run_incident, "baseline": run_baseline}
RESULTS = Path(__file__).parent / "results"


async def run_one(case: Case, system: str, repeat: int) -> RunScore:
    started = time.monotonic()
    try:
        report = await SYSTEMS[system](case.incident, case.world)
    except Exception as exc:  # one broken run must not lose the rest of a paid evaluation
        return RunScore(
            incident_id=case.incident.id,
            scenario=case.scenario,
            split=case.split,
            system=system,
            repeat=repeat,
            valid=False,
            error=type(exc).__name__,
            latency_s=time.monotonic() - started,
        )
    return score_run(case, report, system, repeat, time.monotonic() - started)


def _percent(value) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def render(scores: list[RunScore], model: str) -> str:
    systems = sorted({score.system for score in scores}, reverse=True)
    summaries = {s: summarize([r for r in scores if r.system == s]) for s in systems}
    rows = [
        ("Valid runs", "runs", str),
        ("Invalid runs (excluded)", "invalid_runs", str),
        ("Root-cause accuracy", "accuracy", _percent),
        ("Wrong conclusions", "wrong_conclusion_rate", _percent),
        ("Missed conclusions", "missed_conclusion_rate", _percent),
        ("Fell for misleading evidence", "trap_rate", _percent),
        ("Citation validity", "citation_validity", _percent),
        ("Key evidence recall", "evidence_recall", _percent),
        ("Key evidence precision", "evidence_precision", _percent),
        ("Claims downgraded per run", "downgraded_claims", lambda v: f"{v:.1f}"),
        ("Tool calls per run", "tool_calls", lambda v: f"{v:.1f}"),
        ("LLM calls per run", "llm_calls", lambda v: f"{v:.1f}"),
        ("Tokens per run", "total_tokens", lambda v: f"{v:,.0f}"),
        ("Thinking tokens per run", "thinking_tokens", lambda v: f"{v:,.0f}"),
        ("Mean latency (s)", "latency_s", lambda v: f"{v:.0f}"),
        ("Median latency (s)", "latency_median_s", lambda v: f"{v:.0f}"),
    ]
    lines = [f"# Evaluation results\n\nModel: `{model}`\n"]
    lines.append("| Metric | " + " | ".join(systems) + " |")
    lines.append("|---|" + "---|" * len(systems))
    for label, key, fmt in rows:
        cells = [fmt(summaries[s][key]) if key in summaries[s] else "n/a" for s in systems]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")

    lines.append("\n## Accuracy by split\n")
    lines.append(
        "Prompts and evidence rules were tuned on dev incidents. Holdout incidents were not.\n"
    )
    lines.append("| Split | " + " | ".join(systems) + " |\n|---|" + "---|" * len(systems))
    for split in sorted({score.split for score in scores}):
        cells = []
        for system in systems:
            group = summarize([r for r in scores if r.system == system and r.split == split])
            cells.append(_percent(group.get("accuracy")))
        lines.append(f"| {split} | " + " | ".join(cells) + " |")

    lines.append("\n## By incident (correct runs out of valid runs)\n")
    lines.append("| Incident | Scenario | " + " | ".join(systems) + " |")
    lines.append("|---|---|" + "---|" * len(systems))
    for incident in sorted({score.incident_id for score in scores}):
        group = [r for r in scores if r.incident_id == incident]
        cells = []
        for system in systems:
            runs = [r for r in group if r.system == system and r.valid]
            cells.append(f"{sum(r.correct for r in runs)}/{len(runs)}")
        lines.append(f"| {incident} | {group[0].scenario} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--cases", nargs="*", help="incident ids; default is all")
    parser.add_argument("--systems", nargs="*", default=list(SYSTEMS), choices=list(SYSTEMS))
    parser.add_argument("--golden", action="store_true", help="also run reviewed golden cases")
    parser.add_argument("--incidents", type=Path, help="a different test set, e.g. the blind set")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=2_000_000, help="stop starting new runs")
    parser.add_argument("--out", type=Path, default=RESULTS)
    parser.add_argument("--model", default="", help="label for the report; default is from .env")
    args = parser.parse_args()

    if args.incidents:
        source = load_cases(args.incidents)
    else:
        source = load_golden() if args.golden else load_cases()
    cases = [c for c in source if not args.cases or c.incident.id in args.cases]
    jobs = [(c, s, r) for c in cases for s in args.systems for r in range(args.repeats)]
    gate = asyncio.Semaphore(args.concurrency)
    scores: list[RunScore] = []
    args.out.mkdir(parents=True, exist_ok=True)

    async def worker(case: Case, system: str, repeat: int) -> None:
        async with gate:
            if sum(score.total_tokens for score in scores) >= args.max_tokens:
                return
            score = await run_one(case, system, repeat)
        scores.append(score)
        mark = "ok" if score.correct else ("INVALID" if not score.valid else "WRONG")
        print(
            f"[{len(scores)}/{len(jobs)}] {score.incident_id} {system} #{repeat}: {mark} "
            f"{score.outcome} {score.top_category}/{score.top_component} "
            f"{score.total_tokens} tokens {score.latency_s:.0f}s {score.stop_reason}{score.error}",
            flush=True,
        )
        (args.out / "results.json").write_text(
            json.dumps([s.model_dump() for s in scores], indent=1)
        )

    await asyncio.gather(*(worker(*job) for job in jobs))
    model = args.model or _model_label()
    (args.out / "results.md").write_text(render(scores, model))
    print(render(scores, model))
    skipped = len(jobs) - len(scores)
    if skipped:
        print(f"WARNING: {skipped} runs not started because --max-tokens was reached.")


def _model_label() -> str:
    return f"{os.environ.get('AI_PROVIDER')}:{os.environ.get('AI_MODEL')}"


if __name__ == "__main__":
    asyncio.run(main())
