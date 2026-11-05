"""Deterministic scoring. No LLM judges here, so a score can't be argued with or drift."""

from collections.abc import Sequence
from statistics import mean, median

from pydantic import BaseModel

from investigator.schemas import Case, RankedHypothesis, Report

# Runs ended by these say nothing about the system, so they are excluded rather than scored.
INVALID_STOPS = {"provider_quota_exhausted"}


class RunScore(BaseModel):
    incident_id: str
    scenario: str
    split: str
    system: str
    repeat: int
    valid: bool = True
    error: str = ""
    correct: bool = False
    outcome: str = ""
    expected_outcome: str = ""
    top_category: str = ""
    top_component: str = ""
    top_confidence: str = ""
    stop_reason: str = ""
    outcome_reasons: list[str] = []
    runner_up: str = ""
    wrong_conclusion: bool = False
    missed_conclusion: bool = False
    fell_for_trap: bool = False
    citation_validity: float | None = None
    evidence_precision: float | None = None
    evidence_recall: float | None = None
    downgraded_claims: int = 0
    tool_calls: int = 0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    total_tokens: int = 0
    tokens_by_agent: dict[str, int] = {}
    latency_s: float = 0.0
    gaps: int = 0


def _describe(hypothesis: RankedHypothesis) -> str:
    return f"{hypothesis.category}/{hypothesis.component} net={hypothesis.net_support}"


def score_run(case: Case, report: Report, system: str, repeat: int, latency_s: float) -> RunScore:
    truth = case.truth
    top = report.hypotheses[0] if report.hypotheses else None
    root_cause_right = bool(
        top and top.category == truth.category and top.component == truth.component
    )
    concluded = report.outcome == "conclusive"
    if truth.outcome == "inconclusive":
        correct = not concluded
    else:
        correct = concluded and root_cause_right

    precision = recall = None
    if truth.key_evidence and top:
        cited = set(top.supporting) | set(top.verified_supporting)
        key = set(truth.key_evidence)
        recall = len(cited & key) / len(key)
        precision = len(cited & key) / len(cited) if cited else 0.0

    usage = report.usage
    return RunScore(
        incident_id=case.incident.id,
        scenario=case.scenario,
        split=case.split,
        system=system,
        repeat=repeat,
        valid=report.stop_reason not in INVALID_STOPS,
        correct=correct,
        outcome=report.outcome,
        expected_outcome=truth.outcome,
        top_category=top.category if top else "",
        top_component=top.component if top else "",
        top_confidence=top.confidence if top else "",
        stop_reason=report.stop_reason,
        outcome_reasons=report.outcome_reasons,
        runner_up=_describe(report.hypotheses[1]) if len(report.hypotheses) > 1 else "",
        wrong_conclusion=concluded and not correct,
        missed_conclusion=truth.outcome == "conclusive" and not concluded,
        fell_for_trap=bool(
            concluded and truth.trap_component and top and top.component == truth.trap_component
        ),
        citation_validity=report.grounding.validity,
        evidence_precision=precision,
        evidence_recall=recall,
        downgraded_claims=report.grounding.downgraded_claims,
        tool_calls=usage.tool_calls,
        llm_calls=usage.llm_calls,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        thinking_tokens=usage.thinking_tokens,
        total_tokens=usage.total_tokens,
        tokens_by_agent={a: u.total_tokens for a, u in report.usage_by_agent.items()},
        latency_s=latency_s,
        gaps=len(report.gaps),
    )


def _average(values: Sequence[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return mean(present) if present else None


def summarize(scores: Sequence[RunScore]) -> dict:
    """Totals for one group of runs. Invalid runs are counted but never averaged in."""
    valid = [score for score in scores if score.valid]
    summary = {"runs": len(valid), "invalid_runs": len(scores) - len(valid)}
    if not valid:
        return summary
    summary |= {
        "accuracy": mean(score.correct for score in valid),
        "wrong_conclusion_rate": mean(score.wrong_conclusion for score in valid),
        "missed_conclusion_rate": mean(score.missed_conclusion for score in valid),
        "trap_rate": mean(score.fell_for_trap for score in valid),
        "citation_validity": _average([score.citation_validity for score in valid]),
        "evidence_precision": _average([score.evidence_precision for score in valid]),
        "evidence_recall": _average([score.evidence_recall for score in valid]),
        "downgraded_claims": mean(score.downgraded_claims for score in valid),
        "tool_calls": mean(score.tool_calls for score in valid),
        "llm_calls": mean(score.llm_calls for score in valid),
        "total_tokens": mean(score.total_tokens for score in valid),
        "thinking_tokens": mean(score.thinking_tokens for score in valid),
        "latency_s": mean(score.latency_s for score in valid),
        "latency_median_s": median(score.latency_s for score in valid),
    }
    return summary
