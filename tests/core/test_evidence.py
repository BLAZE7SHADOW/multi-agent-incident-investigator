import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from investigator.evidence import (
    assess_hypothesis,
    clean_actions,
    clean_claim,
    clean_finding,
    clean_hypotheses,
    clean_verification,
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
    HypothesisVerdict,
    RankedHypothesis,
    RowVerdict,
    ToolResult,
    Verification,
)
from investigator.tools import wrap_untrusted

LEDGER = {
    "top-1": Evidence(id="top-1", source="dependencies", content="api depends on db"),
    "his-1": Evidence(id="his-1", source="history", content="similar incident last year"),
    "log-1": Evidence(id="log-1", source="logs", content="error"),
    "log-2": Evidence(id="log-2", source="logs", content="error"),
    "met-1": Evidence(id="met-1", source="metrics", content="spike"),
    "dep-1": Evidence(id="dep-1", source="deployments", content="deploy"),
}
ACCEPT = Critique(verdict="accept", objections=[], requests=[])


def hypothesis(id="h1", supporting=("log-1", "met-1"), contradicting=(), **fields):
    defaults = {"statement": "s", "category": "bad_deploy", "component": "payments"}
    return Hypothesis(
        id=id,
        supporting=list(supporting),
        contradicting=list(contradicting),
        assumptions=[],
        **{**defaults, **fields},
    )


def ranked(id="h1", confidence="medium", net_support=3, **fields):
    return RankedHypothesis(
        **hypothesis(id=id, **fields).model_dump(), confidence=confidence, net_support=net_support
    )


def verdict(hypothesis_id="h1", **labels):
    rows = [
        RowVerdict(evidence_id=k.replace("_", "-"), label=v, reason="r") for k, v in labels.items()
    ]
    return HypothesisVerdict(hypothesis_id=hypothesis_id, rows=rows)


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


# --- Verification ---


def test_clean_verification_drops_unknown_rows_and_hypotheses_and_measures_them():
    raw = Verification(
        verdicts=[
            verdict(log_1="supports", fake_1="supports"),
            verdict("nope", log_1="supports"),
        ]
    )
    cleaned, grounding = clean_verification(raw, [hypothesis()], LEDGER)
    assert [v.hypothesis_id for v in cleaned.verdicts] == ["h1"]
    assert [r.evidence_id for r in cleaned.verdicts[0].rows] == ["log-1"]
    assert grounding == Grounding(citations=2, valid_citations=1)


def test_conflicting_labels_for_a_row_resolve_to_contradicts():
    rows = [
        RowVerdict(evidence_id="log-1", label="supports", reason="r"),
        RowVerdict(evidence_id="log-1", label="irrelevant", reason="r"),
    ]
    raw = Verification(verdicts=[HypothesisVerdict(hypothesis_id="h1", rows=rows)])
    cleaned, _ = clean_verification(raw, [hypothesis()], LEDGER)
    assert [(r.evidence_id, r.label) for r in cleaned.verdicts[0].rows] == [
        ("log-1", "contradicts")
    ]


# --- Computed confidence ---

BOTH = {"log-1": "supports", "met-1": "supports"}


@pytest.mark.parametrize(
    ("label", "h", "labels", "failed", "expected"),
    [
        ("all verified", hypothesis(), BOTH, [], "high"),
        (
            "exactly 4 of 5 cited verified",
            hypothesis(supporting=["log-1", "log-2", "met-1", "dep-1", "met-2"]),
            {"log-1": "supports", "log-2": "supports", "met-1": "supports", "dep-1": "supports"},
            [],
            "high",
        ),
        (
            "3 of 4 cited verified",
            hypothesis(supporting=["log-1", "log-2", "met-1", "dep-1"]),
            {"log-1": "supports", "log-2": "supports", "met-1": "supports"},
            [],
            "medium",
        ),
        (
            "exactly half of cited verified",
            hypothesis(supporting=["log-1", "met-1", "log-2", "dep-1"]),
            BOTH,
            [],
            "medium",
        ),
        (
            "under half of cited verified",
            hypothesis(supporting=["log-1", "met-1", "log-2", "dep-1", "met-2"]),
            BOTH,
            [],
            "low",
        ),
        ("a tool failed", hypothesis(), BOTH, ["get_deployments"], "medium"),
        (
            "one verified contradiction",
            hypothesis(),
            {
                "log-1": "supports",
                "met-1": "supports",
                "dep-1": "contradicts",
            },
            [],
            "medium",
        ),
        (
            "contradictions match support",
            hypothesis(),
            {
                "log-1": "supports",
                "met-1": "supports",
                "dep-1": "contradicts",
                "log-2": "contradicts",
            },
            [],
            "low",
        ),
        (
            "one verified source type",
            hypothesis(supporting=["log-1", "log-2"]),
            {"log-1": "supports", "log-2": "supports"},
            [],
            "low",
        ),
        ("cited but not verified", hypothesis(), {"log-1": "irrelevant"}, [], "low"),
        ("verifier said nothing", hypothesis(), {}, [], "low"),
        ("cites nothing", hypothesis(supporting=[]), BOTH, [], "low"),
        ("unknown cause", hypothesis(category="unknown"), BOTH, [], "low"),
    ],
    ids=lambda v: v if isinstance(v, str) and " " in v else "",
)
def test_confidence_is_computed_from_verification(label, h, labels, failed, expected):
    ledger = {**LEDGER, "met-2": Evidence(id="met-2", source="metrics", content="x")}
    assert assess_hypothesis(h, labels, ledger, failed).confidence == expected


def test_symptom_rows_do_not_count_as_support():
    h = hypothesis()
    result = assess_hypothesis(h, {"log-1": "symptom", "met-1": "symptom"}, LEDGER, [])
    assert result.verified_supporting == [] and result.verified_contradicting == []
    assert (result.verified_ratio, result.net_support, result.confidence) == (0.0, 0, "low")


def test_topology_and_history_never_count_as_evidence():
    h = hypothesis(supporting=["log-1", "met-1", "top-1", "his-1"])
    labels = {**BOTH, "top-1": "supports", "his-1": "supports"}
    result = assess_hypothesis(h, labels, LEDGER, [])
    assert result.verified_supporting == ["log-1", "met-1"]
    assert (result.net_support, result.verified_ratio, result.confidence) == (2, 1.0, "high")


def test_background_rows_cannot_supply_a_second_source_type():
    h = hypothesis(supporting=["log-1", "top-1", "his-1"])
    labels = {"log-1": "supports", "top-1": "supports", "his-1": "supports"}
    assert assess_hypothesis(h, labels, LEDGER, []).confidence == "low"


def test_background_rows_cannot_contradict_either():
    labels = {**BOTH, "top-1": "contradicts", "his-1": "contradicts"}
    result = assess_hypothesis(hypothesis(), labels, LEDGER, [])
    assert result.verified_contradicting == [] and result.confidence == "high"


def test_topology_cannot_outvote_real_observations():
    wrong = hypothesis(id="wrong", supporting=["top-1", "his-1", "log-2", "dep-1"])
    right = hypothesis(id="right", supporting=["log-1", "met-1"])
    verification = Verification(
        verdicts=[
            verdict("wrong", top_1="supports", his_1="supports", dep_1="supports"),
            verdict("right", log_1="supports", met_1="supports"),
        ]
    )
    assert rank_hypotheses([wrong, right], verification, LEDGER, [])[0].id == "right"


def test_computed_metrics_are_exact():
    h = hypothesis(supporting=["log-1", "log-2", "met-1", "dep-1"])
    labels = {
        "log-1": "supports",
        "met-1": "supports",
        "log-2": "irrelevant",
        "dep-1": "contradicts",
    }
    result = assess_hypothesis(h, labels, LEDGER, [])
    assert result.verified_ratio == 0.5
    assert result.net_support == 1
    assert result.verified_supporting == ["log-1", "met-1"]
    assert result.verified_contradicting == ["dep-1"]


def test_uncited_verified_rows_do_not_raise_verified_ratio():
    h = hypothesis(supporting=["log-1", "log-2"])
    labels = {"log-1": "supports", "met-1": "supports", "dep-1": "supports"}
    assert assess_hypothesis(h, labels, LEDGER, []).verified_ratio == 0.5


def test_source_types_count_only_verified_rows():
    h = hypothesis(supporting=["log-1", "met-1"])
    labels = {"log-1": "supports", "log-2": "supports", "met-1": "irrelevant"}
    assert assess_hypothesis(h, labels, LEDGER, []).confidence == "low"


def test_repeating_a_citation_does_not_inflate_verified_ratio():
    h = hypothesis(supporting=["log-1", "log-1", "log-1", "met-1"])
    assert assess_hypothesis(h, {"log-1": "supports"}, LEDGER, []).verified_ratio == 0.5


def test_verifier_can_surface_a_contradiction_the_hypothesis_never_cited():
    labels = {**BOTH, "dep-1": "contradicts"}
    result = assess_hypothesis(hypothesis(contradicting=[]), labels, LEDGER, [])
    assert result.verified_contradicting == ["dep-1"]
    assert result.confidence == "medium"


def test_ranking_follows_verified_evidence_not_citation_count():
    bluff = hypothesis(id="bluff", supporting=["log-1", "log-2", "met-1", "dep-1"])
    real = hypothesis(id="real", supporting=["log-1", "met-1"])
    verification = Verification(
        verdicts=[
            verdict("real", **{"log_1": "supports", "met_1": "supports", "dep_1": "supports"})
        ]
    )
    result = rank_hypotheses([bluff, real], verification, LEDGER, [])
    assert [h.id for h in result] == ["real", "bluff"]


def test_more_verified_evidence_outranks_higher_verified_ratio():
    broad = hypothesis(id="broad", supporting=["log-1", "log-2", "met-1", "dep-1"])
    narrow = hypothesis(id="narrow", supporting=["log-1"])
    verification = Verification(
        verdicts=[
            verdict("broad", log_1="supports", log_2="supports", met_1="supports"),
            verdict("narrow", log_1="supports"),
        ]
    )
    result = rank_hypotheses([narrow, broad], verification, LEDGER, [])
    assert [h.id for h in result] == ["broad", "narrow"]


def test_ranking_uses_verified_ratio_to_break_ties():
    loose = hypothesis(id="loose", supporting=["log-1", "log-2"])
    tight = hypothesis(id="tight", supporting=["log-1"])
    verification = Verification(
        verdicts=[verdict("loose", log_1="supports"), verdict("tight", log_1="supports")]
    )
    result = rank_hypotheses([loose, tight], verification, LEDGER, [])
    assert [h.id for h in result] == ["tight", "loose"]


def test_without_verification_everything_is_low_and_inconclusive():
    result = rank_hypotheses([hypothesis()], None, LEDGER, [])
    assert result[0].confidence == "low"
    assert decide_outcome(result, ACCEPT)[0] == "inconclusive"


# --- Outcome ---


def test_conclusive_when_all_conditions_hold():
    rival = ranked(id="h2", net_support=2)
    assert decide_outcome([ranked(net_support=3), rival], ACCEPT) == ("conclusive", [])


def test_no_rival_needs_no_lead():
    assert decide_outcome([ranked()], ACCEPT)[0] == "conclusive"


@pytest.mark.parametrize(
    ("hypotheses", "critique", "reason"),
    [
        ([], ACCEPT, "no hypotheses"),
        ([ranked()], None, "critic"),
        ([ranked()], Critique(verdict="investigate", objections=[], requests=[]), "critic"),
        ([ranked()], Critique(verdict="insufficient", objections=[], requests=[]), "critic"),
        ([ranked(confidence="low")], ACCEPT, "low confidence"),
        ([ranked(net_support=2), ranked(id="h2", net_support=2)], ACCEPT, "lead"),
    ],
)
def test_inconclusive_cases_say_why(hypotheses, critique, reason):
    outcome, reasons = decide_outcome(hypotheses, critique)
    assert outcome == "inconclusive"
    assert reason in " ".join(reasons)


def test_all_failed_conditions_are_reported():
    _, reasons = decide_outcome([ranked(confidence="low")], None)
    assert len(reasons) == 2


# --- Actions ---


def action(description="Add an alert", risk="low", hypothesis_id="h1"):
    return Action(description=description, risk=risk, hypothesis_id=hypothesis_id)


def test_low_risk_action_needs_no_approval():
    [result] = clean_actions([action()], [ranked()], "conclusive")
    assert result.requires_human_approval is False


def test_llm_high_risk_label_is_respected():
    [result] = clean_actions([action(risk="high")], [ranked()], "conclusive")
    assert result.requires_human_approval is True


def test_action_for_unknown_hypothesis_is_dropped_when_conclusive():
    assert clean_actions([action(hypothesis_id="nope")], [ranked()], "conclusive") == []


def test_inconclusive_keeps_general_actions_but_requires_approval():
    [result] = clean_actions([action(hypothesis_id="")], [ranked()], "inconclusive")
    assert result.requires_human_approval is True
