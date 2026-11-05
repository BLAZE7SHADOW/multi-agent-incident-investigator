"""Rules that must hold for every input, checked on thousands of generated cases."""

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from evals.scoring import score_run
from investigator.evidence import (
    CHANGE_KINDS,
    assess_hypothesis,
    clean_claim,
    decide_outcome,
    rank_hypotheses,
)
from investigator.schemas import (
    Case,
    Claim,
    Critique,
    Evidence,
    Hypothesis,
    HypothesisVerdict,
    Report,
    RowVerdict,
    Usage,
    Verification,
)

SOURCES = ["logs", "metrics", "deployments", "dependencies", "history"]
CATEGORIES = [
    "bad_deploy",
    "config_change",
    "resource_exhaustion",
    "dependency_failure",
    "expired_certificate",
    "traffic_surge",
    "unknown",
]
LABELS = ["supports", "contradicts", "symptom", "irrelevant"]
IDS = [f"r{i}" for i in range(10)]
LEDGER = {
    row_id: Evidence(
        id=row_id,
        source=SOURCES[i % len(SOURCES)],
        content=json.dumps({"id": row_id, "service": "api", "kind": "deploy"}),
    )
    for i, row_id in enumerate(IDS)
}
OBSERVED = {row for row, e in LEDGER.items() if e.source in {"logs", "metrics", "deployments"}}

labels_strategy = st.dictionaries(st.sampled_from(IDS), st.sampled_from(LABELS))
cited_strategy = st.lists(st.sampled_from(IDS))
category_strategy = st.sampled_from(CATEGORIES)
failed_strategy = st.lists(st.sampled_from(["query_logs", "get_deployments"]), max_size=2)
MANY = settings(max_examples=2000, deadline=None)


def make(category, cited, id="h1", component="api"):
    return Hypothesis(
        id=id,
        statement="s",
        category=category,
        component=component,
        supporting=cited,
        contradicting=[],
        assumptions=[],
    )


@MANY
@given(category_strategy, cited_strategy, labels_strategy, failed_strategy)
def test_every_calculated_value_matches_its_definition(category, cited, labels, failed):
    result = assess_hypothesis(make(category, cited), labels, LEDGER, failed)
    supporting = {r for r, label in labels.items() if label == "supports" and r in OBSERVED}
    contradicting = {r for r, label in labels.items() if label == "contradicts" and r in OBSERVED}
    observed_cited = set(cited) & OBSERVED

    assert set(result.verified_supporting) == supporting
    assert set(result.verified_contradicting) == contradicting
    expected_ratio = len(observed_cited & supporting) / len(observed_cited) if observed_cited else 0
    assert result.verified_ratio == expected_ratio
    assert 0 <= result.verified_ratio <= 1
    counted = 0 if result.disqualified else len(supporting)
    assert result.net_support == counted - len(contradicting)


@MANY
@given(category_strategy, cited_strategy, labels_strategy, failed_strategy)
def test_confidence_levels_always_meet_their_definitions(category, cited, labels, failed):
    result = assess_hypothesis(make(category, cited), labels, LEDGER, failed)
    sources = {LEDGER[r].source for r in result.verified_supporting}
    if result.confidence in ("medium", "high"):
        assert not result.disqualified and category != "unknown"
        assert len(sources) >= 2 and result.net_support > 0 and result.verified_ratio >= 0.5
    if result.confidence == "high":
        assert result.verified_ratio >= 0.8
        assert not result.verified_contradicting and not failed


@MANY
@given(cited_strategy, labels_strategy)
def test_background_rows_never_change_the_result(cited, labels):
    foreground = {r: label for r, label in labels.items() if r in OBSERVED}
    with_background = assess_hypothesis(make("traffic_surge", cited), labels, LEDGER, [])
    without = assess_hypothesis(make("traffic_surge", cited), foreground, LEDGER, [])
    assert with_background.net_support == without.net_support
    assert with_background.confidence == without.confidence
    assert with_background.verified_ratio == without.verified_ratio


@MANY
@given(cited_strategy, labels_strategy, st.sampled_from(sorted(OBSERVED)))
def test_adding_a_contradiction_never_raises_confidence(cited, labels, row):
    order = {"low": 0, "medium": 1, "high": 2}
    before = assess_hypothesis(make("traffic_surge", cited), labels, LEDGER, [])
    after = assess_hypothesis(
        make("traffic_surge", cited), {**labels, row: "contradicts"}, LEDGER, []
    )
    assert order[after.confidence] <= order[before.confidence]


@MANY
@given(st.sampled_from(sorted(CHANGE_KINDS)), cited_strategy, labels_strategy)
def test_change_cause_is_never_confident_without_its_kind_of_row(category, cited, labels):
    data = {
        row: Evidence(
            id=row,
            source=e.source,
            content=json.dumps({"id": row, "service": "api", "kind": "something_else"}),
        )
        for row, e in LEDGER.items()
    }
    result = assess_hypothesis(make(category, cited), labels, data, [])
    assert result.confidence == "low" and result.disqualified


@st.composite
def hypothesis_sets(draw):
    count = draw(st.integers(1, 4))
    hypotheses = [
        make(draw(category_strategy), draw(cited_strategy), id=f"h{i}") for i in range(count)
    ]
    verdicts = [
        HypothesisVerdict(
            hypothesis_id=h.id,
            rows=[
                RowVerdict(evidence_id=r, label=label, reason="r")
                for r, label in draw(labels_strategy).items()
            ],
        )
        for h in hypotheses
    ]
    return hypotheses, Verification(verdicts=verdicts)


@MANY
@given(hypothesis_sets(), st.sampled_from(["accept", "investigate", "insufficient"]))
def test_conclusive_always_means_every_condition_held(data, verdict):
    hypotheses, verification = data
    ranked = rank_hypotheses(hypotheses, verification, LEDGER, [])
    critique = Critique(verdict=verdict, objections=[], requests=[])
    outcome, reasons = decide_outcome(ranked, critique)
    assert (outcome == "conclusive") == (not reasons)
    if outcome == "conclusive":
        top = ranked[0]
        assert verdict == "accept" and top.confidence != "low" and not top.disqualified
        for rival in ranked[1:]:
            if rival.category != "unknown" and (rival.category, rival.component) != (
                top.category,
                top.component,
            ):
                assert top.net_support - rival.net_support >= 1


@MANY
@given(hypothesis_sets())
def test_ranking_is_ordered_and_loses_nothing(data):
    hypotheses, verification = data
    ranked = rank_hypotheses(hypotheses, verification, LEDGER, [])
    assert sorted(h.id for h in ranked) == sorted(h.id for h in hypotheses)
    keys = [(not h.disqualified, h.net_support, h.verified_ratio) for h in ranked]
    assert keys == sorted(keys, reverse=True)


@MANY
@given(
    st.sampled_from(["fact", "inference", "assumption"]),
    st.lists(st.sampled_from([*IDS, "x", "y"])),
)
def test_a_claim_never_keeps_an_invented_citation(kind, ids):
    claim, grounding = clean_claim(Claim(text="t", kind=kind, evidence_ids=ids), LEDGER)
    assert set(claim.evidence_ids) <= set(IDS)
    assert grounding.citations == len(ids)
    assert grounding.valid_citations == len(claim.evidence_ids)
    assert 0 <= grounding.validity <= 1
    if not claim.evidence_ids:
        assert claim.kind == "assumption"


CASE = Case.model_validate(
    {
        "incident": {
            "id": "X",
            "title": "t",
            "description": "d",
            "service": "s",
            "reported_at": "r",
        },
        "scenario": "s",
        "world": {"logs": [{"id": row} for row in IDS]},
        "truth": {
            "outcome": "conclusive",
            "category": "traffic_surge",
            "component": "api",
            "key_evidence": ["r0", "r1", "r2"],
        },
    }
)


@MANY
@given(
    cited_strategy, cited_strategy, st.lists(st.sampled_from(["r0", "r1", "r2", "r3"]), min_size=1)
)
def test_precision_and_recall_match_a_row_by_row_count(cited, verified, key):
    """A second implementation that counts row by row, sharing no code with the scorer."""
    case = CASE.model_copy(update={"truth": CASE.truth.model_copy(update={"key_evidence": key})})
    top = assess_hypothesis(
        make("traffic_surge", cited), {r: "supports" for r in verified}, LEDGER, []
    )
    # Scorer's "cited" includes verified rows; background rows are dropped from verified.
    report = Report(
        incident_id="X",
        outcome="conclusive",
        stop_reason="s",
        summary="s",
        hypotheses=[top],
        facts=[],
        assumptions=[],
        gaps=[],
        actions=[],
        usage=Usage(),
    )
    score = score_run(case, report, "m", 0, 0.0)

    predicted = []
    for row in cited + top.verified_supporting:
        if row not in predicted:
            predicted.append(row)
    true_positive = false_positive = 0
    for row in predicted:
        if row in key:
            true_positive += 1
        else:
            false_positive += 1
    distinct_key = []
    for row in key:
        if row not in distinct_key:
            distinct_key.append(row)
    false_negative = sum(1 for row in distinct_key if row not in predicted)

    expected_recall = true_positive / (true_positive + false_negative)
    expected_precision = (
        true_positive / (true_positive + false_positive) if true_positive + false_positive else 0
    )
    assert score.evidence_recall == expected_recall
    assert score.evidence_precision == expected_precision
