"""Investigate one test incident: uv run python cli.py INC-01"""

import argparse
import asyncio
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

from investigator.schemas import Report
from investigator.tools import load_cases
from reliability.runtime import run_incident

RUNS = Path("runs")


def render(report: Report) -> str:
    lines = [
        f"Incident: {report.incident_id}",
        f"Outcome: {report.outcome.upper()} (stopped: {report.stop_reason})",
        *[f"  Why: {reason}" for reason in report.outcome_reasons],
        f"\n{report.summary}\n",
        "Hypotheses:",
    ]
    for rank, h in enumerate(report.hypotheses, 1):
        lines += [
            f"  {rank}. [{h.confidence}] {h.statement} ({h.category}, {h.component})",
            f"     supports: {h.verified_supporting}  contradicts: {h.verified_contradicting}",
            f"     verified ratio: {h.verified_ratio:.2f}  net support: {h.net_support}",
            *([f"     disqualified: {h.disqualified}"] if h.disqualified else []),
        ]
    lines.append(f"\nFacts: {len(report.facts)}  Inferences: {len(report.inferences)}")
    lines.append(f"Assumptions: {len(report.assumptions)}")
    lines += ["Gaps:", *[f"  - {gap}" for gap in report.gaps]]
    lines.append("Actions:")
    for action in report.actions:
        approval = " [needs approval]" if action.requires_human_approval else ""
        lines.append(f"  - {action.description}{approval}")
    usage = report.usage
    lines.append(
        f"\nUsage: {usage.tool_calls} tool calls, {usage.llm_calls} LLM calls, "
        f"{usage.total_tokens} tokens ({usage.thinking_tokens} thinking). "
        f"Citation validity: {report.grounding.validity:.0%}"
    )
    for agent, spent in sorted(report.usage_by_agent.items(), key=lambda a: -a[1].total_tokens):
        lines.append(
            f"  {agent:14} {spent.llm_calls:>3} calls  {spent.input_tokens:>6} in  "
            f"{spent.output_tokens:>6} out  {spent.thinking_tokens:>6} thinking"
        )
    return "\n".join(lines)


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("incident_id")
    parser.add_argument("--resume", metavar="THREAD", help="continue a crashed run's thread")
    args = parser.parse_args()

    cases = {case.incident.id: case for case in load_cases()}
    if args.incident_id not in cases:
        parser.error(f"unknown incident; choose from {sorted(cases)}")
    case = cases[args.incident_id]

    RUNS.mkdir(exist_ok=True)
    thread = args.resume or f"{args.incident_id}-{uuid4().hex[:8]}"
    print(f"Thread: {thread}")
    report = await run_incident(
        case.incident,
        case.world,
        db_path=RUNS / "checkpoints.sqlite",
        thread_id=thread,
        resume=bool(args.resume),
    )
    (RUNS / f"{args.incident_id}.json").write_text(report.model_dump_json(indent=2))
    print(render(report))


if __name__ == "__main__":
    asyncio.run(main())
