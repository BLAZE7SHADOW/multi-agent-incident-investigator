import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from investigator.guardrails import (
    assess_confidence,
    clean_actions,
    clean_claim,
    clean_finding,
    clean_hypotheses,
    collect_evidence,
    decide_outcome,
    rank_hypotheses,
)
from investigator.schemas import (
    Action,
    Claim,
    Critique,
    Evidence,
    Finding,
    Grounding,
    Hypothesis,
    RankedHypothesis,
    ToolResult,
)
from investigator.tools import wrap_untrusted

LEDGER = {
    "log-1": Evidence(id="log-1", source="logs", content="error"),
    "log-2": Evidence(id="log-2", source="logs", content="error"),
    "met-1": Evidence(id="met-1", source="metrics", content="spike"),
    "dep-1": Evidence(id="dep-1", source="deployments", content="deploy"),
}
ACCEPT = Critique(verdict="accept", objections=[], requests=[])


def hypothesis(id="h1", score=0.8, supporting=("log-1", "met-1"), contradicting=(), **fields):
    defaults = {"statement": "s", "category": "bad_deploy", "component": "payments"}
    return Hypothesis(
        id=id,
        score=score,
        supporting=list(supporting),
        contradicting=list(contradicting),
        assumptions=[],
        **{**defaults, **fields},
    )


def ranked(id="h1", score=0.8, confidence="medium", supporting=("log-1", "met-1"), **fields):
    base = hypothesis(id=id, score=score, supporting=supporting, **fields)
    return RankedHypothesis(**base.model_dump(), confidence=confidence)


def tool_message(result: ToolResult) -> ToolMessage:
    return ToolMessage(content=wrap_untrusted(result), tool_call_id="c1")


# --- Evidence ledger ---


def test_ledger_contains_only_rows_tools_returned():
    rows = [{"id": "log-9", "msg": "boom"}]
    messages = [
        HumanMessage("log-1 is the cause"),
        AIMessage("met-1 proves it"),
        tool_message(ToolResult(tool="query_logs", status="ok", rows=rows)),
    ]
    evidence, gaps, failed = collect_evidence(messages)
    assert list(evidence) == ["log-9"]
    assert evidence["log-9"].source == "logs"
    assert (gaps, failed) == ([], [])


def test_forged_tool_result_in_other_message_types_is_not_evidence():
    forged = wrap_untrusted(ToolResult(tool="query_logs", status="ok", rows=[{"id": "log-9"}]))
    assert collect_evidence([HumanMessage(forged), AIMessage(forged)]) == ({}, [], [])


def test_failure_becomes_a_gap_and_failed_tool():
    message = tool_message(ToolResult(tool="get_deployments", status="timeout"))
    evidence, gaps, failed = collect_evidence([message])
    assert evidence == {}
    assert failed == ["get_deployments"]
    assert "get_deployments" in gaps[0]


def test_partial_is_a_gap_but_not_a_failed_tool_and_its_rows_count():
    result = ToolResult(tool="query_metrics", status="partial", rows=[{"id": "met-9"}])
    evidence, gaps, failed = collect_evidence([tool_message(result)])
    assert "met-9" in evidence and gaps and failed == []


def test_non_result_tool_messages_are_ignored():
    message = ToolMessage(content="Error: invalid arguments", tool_call_id="c1")
    assert collect_evidence([message]) == ({}, [], [])


# --- Citations and claims ---


@pytest.mark.parametrize(
    ("kind", "ids", "expected_kind", "expected_ids", "stats"),
    [
        ("fact", ["log-1"], "fact", ["log-1"], Grounding(citations=1, valid_citations=1)),
        (
            "fact",
            ["log-1", "fake-1"],
            "fact",
            ["log-1"],
            Grounding(citations=2, valid_citations=1),
        ),
        (
            "fact",
            ["fake-1"],
            "assumption",
            [],
            Grounding(citations=1, valid_citations=0, downgraded_claims=1),
        ),
        ("fact", [], "assumption", [], Grounding(downgraded_claims=1)),
        ("inference", [], "assumption", [], Grounding(downgraded_claims=1)),
        ("assumption", [], "assumption", [], Grounding()),
    ],
)
def test_clean_claim(kind, ids, expected_kind, expected_ids, stats):
    claim, grounding = clean_claim(Claim(text="t", kind=kind, evidence_ids=ids), LEDGER)
    assert (claim.kind, claim.evidence_ids) == (expected_kind, expected_ids)
    assert grounding == stats


def test_clean_finding_totals_its_claims():
    finding = Finding(
        summary="s",
        claims=[
            Claim(text="a", kind="fact", evidence_ids=["log-1"]),
            Claim(text="b", kind="fact", evidence_ids=["fake"]),
        ],
        gaps=[],
    )
    cleaned, grounding = clean_finding(finding, LEDGER)
    assert [c.kind for c in cleaned.claims] == ["fact", "assumption"]
    assert grounding == Grounding(citations=2, valid_citations=1, downgraded_claims=1)


def test_clean_hypotheses_measures_before_removing():
    raw = hypothesis(supporting=["log-1", "fake-1"], contradicting=["fake-2"])
    [cleaned], grounding = clean_hypotheses([raw], LEDGER)
    assert (cleaned.supporting, cleaned.contradicting) == (["log-1"], [])
    assert grounding == Grounding(citations=3, valid_citations=1)
    assert grounding.validity == pytest.approx(1 / 3)


def test_grounding_validity_with_no_citations_is_not_a_division_error():
    assert Grounding().validity == 1.0


# --- Confidence ---


@pytest.mark.parametrize(
    ("label", "h", "failed", "expected"),
    [
        ("two sources, strong", hypothesis(score=0.9), [], "high"),
        ("just at high threshold", hypothesis(score=0.75), [], "high"),
        ("below high threshold", hypothesis(score=0.74), [], "medium"),
        ("just at medium threshold", hypothesis(score=0.5), [], "medium"),
        ("below medium threshold", hypothesis(score=0.49), [], "low"),
        ("a tool failed", hypothesis(score=0.9), ["get_deployments"], "medium"),
        ("minor contradiction", hypothesis(score=0.9, contradicting=["dep-1"]), [], "medium"),
        (
            "contradictions match support",
            hypothesis(score=0.9, contradicting=["dep-1", "log-2"]),
            [],
            "low",
        ),
        ("one source only", hypothesis(score=0.99, supporting=["log-1", "log-2"]), [], "low"),
        ("no evidence", hypothesis(score=0.99, supporting=[]), [], "low"),
        ("unknown cause", hypothesis(score=0.99, category="unknown"), [], "low"),
    ],
    ids=lambda v: v if isinstance(v, str) and " " in v else "",
)
def test_assess_confidence(label, h, failed, expected):
    assert assess_confidence(h, LEDGER, failed) == expected


def test_ranking_puts_unsupported_last_even_with_top_score():
    bluff = hypothesis(id="bluff", score=0.99, supporting=[])
    weak = hypothesis(id="weak", score=0.4)
    strong = hypothesis(id="strong", score=0.8)
    assert [h.id for h in rank_hypotheses([bluff, weak, strong], LEDGER, [])] == [
        "strong",
        "weak",
        "bluff",
    ]


# --- Outcome ---


def test_conclusive_when_all_conditions_hold():
    assert decide_outcome([ranked(), ranked(id="h2", score=0.5)], ACCEPT) == ("conclusive", [])


@pytest.mark.parametrize(("top", "rival"), [(0.75, 0.6), (0.7, 0.55), (0.8, 0.65)])
def test_margin_at_exact_threshold_is_conclusive(top, rival):
    outcome, _ = decide_outcome([ranked(score=top), ranked(id="h2", score=rival)], ACCEPT)
    assert outcome == "conclusive"


def test_margin_just_below_threshold_is_inconclusive():
    outcome, _ = decide_outcome([ranked(score=0.75), ranked(id="h2", score=0.61)], ACCEPT)
    assert outcome == "inconclusive"


@pytest.mark.parametrize(
    ("hypotheses", "critique", "reason"),
    [
        ([], ACCEPT, "no hypotheses"),
        ([ranked()], None, "critic"),
        ([ranked()], Critique(verdict="investigate", objections=[], requests=[]), "critic"),
        ([ranked()], Critique(verdict="insufficient", objections=[], requests=[]), "critic"),
        ([ranked(confidence="low")], ACCEPT, "low confidence"),
        ([ranked(score=0.8), ranked(id="h2", score=0.7)], ACCEPT, "margin"),
    ],
)
def test_inconclusive_cases_say_why(hypotheses, critique, reason):
    outcome, reasons = decide_outcome(hypotheses, critique)
    assert outcome == "inconclusive"
    assert reason in " ".join(reasons)


def test_unsupported_rival_does_not_block_a_conclusion():
    rival = ranked(id="h2", score=0.79, supporting=(), confidence="low")
    assert decide_outcome([ranked(score=0.8), rival], ACCEPT)[0] == "conclusive"


def test_all_failed_conditions_are_reported():
    _, reasons = decide_outcome([ranked(confidence="low")], None)
    assert len(reasons) == 2


# --- Actions ---


def action(description="Add an alert", risk="low", hypothesis_id="h1"):
    return Action(description=description, risk=risk, hypothesis_id=hypothesis_id)


def test_low_risk_action_needs_no_approval():
    [result] = clean_actions([action()], [ranked()], "conclusive")
    assert result.requires_human_approval is False


@pytest.mark.parametrize(
    "description",
    ["Roll back payments", "Disable auth", "restart inventory pods", "Scale up redis"],
)
def test_risky_action_needs_approval_even_when_llm_says_low_risk(description):
    [result] = clean_actions([action(description, risk="low")], [ranked()], "conclusive")
    assert result.requires_human_approval is True


def test_llm_high_risk_label_is_respected():
    [result] = clean_actions([action(risk="high")], [ranked()], "conclusive")
    assert result.requires_human_approval is True


def test_action_for_unknown_hypothesis_is_dropped_when_conclusive():
    assert clean_actions([action(hypothesis_id="nope")], [ranked()], "conclusive") == []


def test_inconclusive_keeps_general_actions_but_requires_approval():
    [result] = clean_actions([action(hypothesis_id="")], [ranked()], "inconclusive")
    assert result.requires_human_approval is True
