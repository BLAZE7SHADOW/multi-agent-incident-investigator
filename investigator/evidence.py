"""Evidence rules required for every conclusion. LLMs judge meaning; this code decides."""

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from statistics import mean

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
    RowLabel,
    RowVerdict,
    Source,
    Verification,
)
from investigator.tools import read_tool_result

# Fixed before any evaluation run. Do not tune these against the test incidents.
HIGH_VERIFIED_RATIO = 0.8
MEDIUM_VERIFIED_RATIO = 0.5
MIN_SOURCES = 2
MIN_LEAD = 1
RECOVERY_RATIO = 0.5  # errors must fall below this share of their peak to count as recovered
CHANGE_KINDS = {"bad_deploy": "deploy", "config_change": "config"}

# What happened during this incident. Topology and past incidents are useful leads, but they exist
# whatever the cause, so they can never count for or against a hypothesis.
OBSERVATIONS: frozenset[Source] = frozenset({"logs", "metrics", "deployments"})

_TOOL_SOURCE: dict[str, Source] = {
    "query_logs": "logs",
    "query_metrics": "metrics",
    "get_deployments": "deployments",
    "get_dependencies": "dependencies",
    "search_incidents": "history",
}


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
                id=row["id"], source=_TOOL_SOURCE[result.tool], content=json.dumps(row)
            )
        if result.status in ("timeout", "error"):
            failed.append(result.tool)
            gaps.append(f"{result.tool} unavailable: {result.status}")
        elif result.status == "partial":
            gaps.append(f"{result.tool} returned partial results")
        elif result.status == "skipped":
            gaps.append(f"{result.tool} skipped: {result.note}")
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


def clean_verification(
    verification: Verification,
    hypotheses: Sequence[Hypothesis],
    ledger: Mapping[str, Evidence],
) -> tuple[Verification, Grounding]:
    """Drop labels for unknown hypotheses or rows. Conflicting labels resolve to `contradicts`."""
    known = {hypothesis.id for hypothesis in hypotheses}
    grounding = Grounding()
    verdicts = []
    for verdict in verification.verdicts:
        if verdict.hypothesis_id not in known:
            continue
        kept: dict[str, RowVerdict] = {}
        for row in verdict.rows:
            grounding += Grounding(citations=1, valid_citations=int(row.evidence_id in ledger))
            if row.evidence_id not in ledger:
                continue
            seen = kept.get(row.evidence_id)
            if seen and seen.label != row.label:
                row = row.model_copy(update={"label": "contradicts"})
            kept[row.evidence_id] = row
        verdicts.append(verdict.model_copy(update={"rows": list(kept.values())}))
    return Verification(verdicts=verdicts), grounding


def _row(evidence: Evidence) -> dict:
    try:
        row = json.loads(evidence.content)
    except ValueError:
        return {}
    return row if isinstance(row, dict) else {}


def _missing_change_evidence(
    hypothesis: Hypothesis, supporting: Iterable[str], ledger: Mapping[str, Evidence]
) -> str:
    """A deploy or config cause needs a verified change of that kind on the component blamed."""
    kind = CHANGE_KINDS.get(hypothesis.category)
    if kind is None:
        return ""
    for row_id in supporting:
        row = _row(ledger[row_id])
        if (
            ledger[row_id].source == "deployments"
            and row.get("kind") == kind
            and row.get("service") == hypothesis.component
        ):
            return ""
    return f"no verified {kind} row for {hypothesis.component}"


def _split_series(row: dict, at: datetime) -> tuple[list[float], list[float]]:
    """Metric values at or before a moment, and values after it."""
    first = datetime.fromisoformat(row["start"])
    before: list[float] = []
    after: list[float] = []
    for index, value in enumerate(row["values"]):
        moment = first + timedelta(minutes=index * row["step_min"])
        (before if moment <= at else after).append(value)
    return before, after


def _failed_rollback(hypothesis: Hypothesis, ledger: Mapping[str, Evidence]) -> tuple[str, str]:
    """A rolled-back change can't be the cause if errors did not fall. Returns (reason, row id)."""
    if hypothesis.category not in CHANGE_KINDS:
        return "", ""
    rows = [(evidence, _row(evidence)) for evidence in ledger.values()]
    rollbacks = [
        (row["ts"], evidence.id)
        for evidence, row in rows
        if evidence.source == "deployments"
        and row.get("kind") == "rollback"
        and row.get("service") == hypothesis.component
    ]
    if not rollbacks:
        return "", ""
    moment, rollback_id = min(rollbacks)
    at = datetime.fromisoformat(moment)
    for evidence, row in rows:
        if (
            evidence.source != "metrics"
            or row.get("service") != hypothesis.component
            or "error" not in str(row.get("metric", ""))
        ):
            continue
        before, after = _split_series(row, at)
        peak = max(before, default=0)
        if peak > 0 and after and mean(after) >= RECOVERY_RATIO * peak:
            reason = f"{rollback_id} rolled it back, but {evidence.id} shows errors continued"
            return reason, rollback_id
    return "", ""


def assess_hypothesis(
    hypothesis: Hypothesis,
    labels: Mapping[str, RowLabel],
    ledger: Mapping[str, Evidence],
    failed_tools: Iterable[str],
) -> RankedHypothesis:
    """Compute a hypothesis's standing from Verifier labels. Unlabelled rows count for nothing."""
    observed = {row: label for row, label in labels.items() if ledger[row].source in OBSERVATIONS}
    supporting = sorted(row for row, label in observed.items() if label == "supports")
    contradicting = sorted(row for row, label in observed.items() if label == "contradicts")
    cited = {row for row in hypothesis.supporting if ledger[row].source in OBSERVATIONS}
    verified_ratio = len(cited & set(supporting)) / len(cited) if cited else 0.0

    rollback_reason, rollback_row = _failed_rollback(hypothesis, ledger)
    if rollback_row and rollback_row not in contradicting:
        contradicting = sorted([*contradicting, rollback_row])
    disqualified = _missing_change_evidence(hypothesis, supporting, ledger) or rollback_reason

    counted = [] if disqualified else supporting
    sources = {ledger[row].source for row in counted}
    net_support = len(counted) - len(contradicting)

    confidence: Confidence = "low"
    # A disqualified hypothesis counts no support, so the source check below already fails.
    if (
        hypothesis.category != "unknown"
        and len(sources) >= MIN_SOURCES
        and net_support > 0
        and verified_ratio >= MEDIUM_VERIFIED_RATIO
    ):
        strong = verified_ratio >= HIGH_VERIFIED_RATIO and not contradicting
        confidence = "high" if strong and not set(failed_tools) else "medium"

    return RankedHypothesis(
        **hypothesis.model_dump(),
        verified_supporting=supporting,
        verified_contradicting=contradicting,
        verified_ratio=verified_ratio,
        net_support=net_support,
        confidence=confidence,
        disqualified=disqualified,
        row_labels=dict(labels),
    )


def rank_hypotheses(
    hypotheses: Sequence[Hypothesis],
    verification: Verification | None,
    ledger: Mapping[str, Evidence],
    failed_tools: Iterable[str],
) -> list[RankedHypothesis]:
    """Order by verified evidence. With no verification, every hypothesis stays low confidence."""
    failed = list(failed_tools)
    verdicts = verification.verdicts if verification else []
    labels = {v.hypothesis_id: {row.evidence_id: row.label for row in v.rows} for v in verdicts}
    assessed = [assess_hypothesis(h, labels.get(h.id, {}), ledger, failed) for h in hypotheses]
    return sorted(
        assessed,
        key=lambda h: (not h.disqualified, h.net_support, h.verified_ratio),
        reverse=True,
    )


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
    # "Unknown" and a rewording of the same cause are not competing explanations.
    rivals = [
        h
        for h in ranked[1:]
        if h.category != "unknown" and (h.category, h.component) != (top.category, top.component)
    ]
    if rivals and top.net_support - rivals[0].net_support < MIN_LEAD:
        reasons.append(f"no clear lead over the alternative '{rivals[0].id}'")
    return ("inconclusive" if reasons else "conclusive"), reasons


def clean_actions(
    actions: Sequence[Action], ranked: Sequence[RankedHypothesis], outcome: Outcome
) -> list[RecommendedAction]:
    """Actions are recommendations only, and always need approval if the cause is unconfirmed."""
    known = {hypothesis.id for hypothesis in ranked}
    cleaned = []
    for action in actions:
        if outcome == "conclusive" and action.hypothesis_id not in known:
            continue
        needs_approval = outcome == "inconclusive" or action.risk == "high"
        cleaned.append(
            RecommendedAction(**action.model_dump(), requires_human_approval=needs_approval)
        )
    return cleaned
