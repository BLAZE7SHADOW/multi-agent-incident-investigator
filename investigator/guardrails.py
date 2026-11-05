"""Deterministic checks on LLM output. LLMs judge the evidence; this code decides what stands."""

import re
from collections.abc import Iterable, Mapping, Sequence

from langchain_core.messages import BaseMessage

from investigator.schemas import (
    Action,
    Claim,
    Confidence,
    Critique,
    Evidence,
    Finding,
    Grounding,
    Hypothesis,
    Outcome,
    RankedHypothesis,
    RecommendedAction,
    Source,
)
from investigator.tools import read_tool_result

# Fixed before any evaluation run. Do not tune these against the golden set.
HIGH_SCORE = 0.75
MEDIUM_SCORE = 0.5
MIN_MARGIN = 0.15
MIN_SOURCES = 2

_TOOL_SOURCE: dict[str, Source] = {
    "query_logs": "logs",
    "query_metrics": "metrics",
    "get_deployments": "deployments",
    "get_dependencies": "dependencies",
    "search_incidents": "history",
}
_RISKY_ACTION = re.compile(
    r"\b(rollback|roll back|revert|restart|redeploy|delete|drop|disable|failover|fail over"
    r"|scale|migrate|rotate|purge|flush|kill|terminate|shut ?down)",
    re.IGNORECASE,
)


def collect_evidence(
    messages: Iterable[BaseMessage],
) -> tuple[dict[str, Evidence], list[str], list[str]]:
    """Build the ledger from tool messages, the only way a row can become citable.

    Returns evidence by ID, human-readable gaps, and tools that failed outright.
    """
    evidence: dict[str, Evidence] = {}
    gaps: list[str] = []
    failed: list[str] = []
    for message in messages:
        if message.type != "tool":
            continue
        try:
            result = read_tool_result(message.content)
        except ValueError:  # a validation or limit message rather than a tool result
            continue
        for row in result.rows:
            evidence[row["id"]] = Evidence(
                id=row["id"], source=_TOOL_SOURCE[result.tool], content=str(row)
            )
        if result.status in ("timeout", "error"):
            failed.append(result.tool)
            gaps.append(f"{result.tool} unavailable: {result.status}")
        elif result.status == "partial":
            gaps.append(f"{result.tool} returned partial results")
    return evidence, gaps, failed


def _keep_known(ids: Sequence[str], ledger: Mapping[str, Evidence]) -> tuple[list[str], Grounding]:
    kept = [evidence_id for evidence_id in ids if evidence_id in ledger]
    return kept, Grounding(citations=len(ids), valid_citations=len(kept))


def clean_claim(claim: Claim, ledger: Mapping[str, Evidence]) -> tuple[Claim, Grounding]:
    """Drop unknown citations. A fact or inference left with no evidence becomes an assumption."""
    kept, grounding = _keep_known(claim.evidence_ids, ledger)
    kind = claim.kind if kept else "assumption"
    if kind != claim.kind:
        grounding += Grounding(downgraded_claims=1)
    return claim.model_copy(update={"evidence_ids": kept, "kind": kind}), grounding


def clean_finding(finding: Finding, ledger: Mapping[str, Evidence]) -> tuple[Finding, Grounding]:
    grounding = Grounding()
    claims = []
    for claim in finding.claims:
        cleaned, stats = clean_claim(claim, ledger)
        claims.append(cleaned)
        grounding += stats
    return finding.model_copy(update={"claims": claims}), grounding


def clean_hypotheses(
    hypotheses: Sequence[Hypothesis], ledger: Mapping[str, Evidence]
) -> tuple[list[Hypothesis], Grounding]:
    grounding = Grounding()
    cleaned = []
    for hypothesis in hypotheses:
        supporting, support_stats = _keep_known(hypothesis.supporting, ledger)
        contradicting, contra_stats = _keep_known(hypothesis.contradicting, ledger)
        grounding += support_stats + contra_stats
        update = {"supporting": supporting, "contradicting": contradicting}
        cleaned.append(hypothesis.model_copy(update=update))
    return cleaned, grounding


def assess_confidence(
    hypothesis: Hypothesis, ledger: Mapping[str, Evidence], failed_tools: Iterable[str]
) -> Confidence:
    """Cap the LLM's self-reported score by what the evidence can actually show."""
    sources = {ledger[evidence_id].source for evidence_id in hypothesis.supporting}
    contradictions = len(hypothesis.contradicting)
    if (
        hypothesis.category == "unknown"
        or len(sources) < MIN_SOURCES
        or contradictions >= len(hypothesis.supporting)
        or hypothesis.score < MEDIUM_SCORE
    ):
        return "low"
    if hypothesis.score >= HIGH_SCORE and not contradictions and not set(failed_tools):
        return "high"
    return "medium"


def rank_hypotheses(
    hypotheses: Sequence[Hypothesis],
    ledger: Mapping[str, Evidence],
    failed_tools: Iterable[str],
) -> list[RankedHypothesis]:
    """Order by score, with unsupported hypotheses always last whatever they claim."""
    failed = list(failed_tools)
    ordered = sorted(hypotheses, key=lambda h: (bool(h.supporting), h.score), reverse=True)
    return [
        RankedHypothesis(**h.model_dump(), confidence=assess_confidence(h, ledger, failed))
        for h in ordered
    ]


def decide_outcome(
    ranked: Sequence[RankedHypothesis], critique: Critique | None
) -> tuple[Outcome, list[str]]:
    """Conclusive only if every condition holds. Returns the reasons when it does not."""
    if not ranked:
        return "inconclusive", ["no hypotheses were produced"]
    top = ranked[0]
    reasons = []
    if critique is None or critique.verdict != "accept":
        reasons.append("the critic did not accept the conclusion")
    if top.confidence == "low":
        reasons.append("the top hypothesis has low confidence")
    rivals = [h for h in ranked[1:] if h.supporting]
    # Rounded so equal gaps get equal verdicts despite floating-point error.
    if rivals and round(top.score - rivals[0].score, 6) < MIN_MARGIN:
        reasons.append(f"no clear margin over the alternative '{rivals[0].id}'")
    return ("inconclusive" if reasons else "conclusive"), reasons


def clean_actions(
    actions: Sequence[Action], ranked: Sequence[RankedHypothesis], outcome: Outcome
) -> list[RecommendedAction]:
    """Actions are recommendations only. Approval needs can be raised here but never lowered."""
    known = {hypothesis.id for hypothesis in ranked}
    cleaned = []
    for action in actions:
        if outcome == "conclusive" and action.hypothesis_id not in known:
            continue
        needs_approval = (
            outcome == "inconclusive"
            or action.risk == "high"
            or bool(_RISKY_ACTION.search(action.description))
        )
        cleaned.append(
            RecommendedAction(**action.model_dump(), requires_human_approval=needs_approval)
        )
    return cleaned
