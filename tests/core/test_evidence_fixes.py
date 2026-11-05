"""Fix 1 and Fix 2, with every expected value worked out by hand in the comments."""

import json

import pytest

from investigator.evidence import assess_hypothesis, decide_outcome, rank_hypotheses
from investigator.schemas import (
    Critique,
    Evidence,
    Hypothesis,
    HypothesisVerdict,
    RankedHypothesis,
    RowVerdict,
    Verification,
)

ACCEPT = Critique(verdict="accept", objections=[], requests=[])


def row(id, source, **fields):
    return Evidence(id=id, source=source, content=json.dumps({"id": id, **fields}))


def ledger(*rows):
    return {r.id: r for r in rows}


LOG = row("log-1", "logs", service="pay", msg="boom")
DEPLOY = row("dep-1", "deployments", ts="2026-01-01T10:00", service="pay", kind="deploy")
CONFIG = row("dep-2", "deployments", ts="2026-01-01T10:00", service="pay", kind="config")
OTHER = row("dep-3", "deployments", ts="2026-01-01T10:00", service="db", kind="deploy")


def hypothesis(category="bad_deploy", component="pay", supporting=(), id="h1"):
    return Hypothesis(
        id=id,
        statement="s",
        category=category,
        component=component,
        supporting=list(supporting),
        contradicting=[],
        assumptions=[],
    )


# --- Fix 1: a change cause needs a verified change ---


def test_deploy_cause_with_a_verified_deploy_stands():
    # supports: dep-1, log-1 => net 2, 2 source types, ratio 2/2 => high
    result = assess_hypothesis(
        hypothesis(supporting=["dep-1", "log-1"]),
        {"dep-1": "supports", "log-1": "supports"},
        ledger(DEPLOY, LOG),
        [],
    )
    assert (result.net_support, result.confidence, result.disqualified) == (2, "high", "")


@pytest.mark.parametrize(
    ("category", "rows", "labels", "why"),
    [
        ("bad_deploy", [LOG], {"log-1": "supports"}, "no deployment rows at all"),
        ("config_change", [LOG], {"log-1": "supports"}, "no config rows at all"),
        ("bad_deploy", [CONFIG, LOG], {"dep-2": "supports", "log-1": "supports"}, "wrong kind"),
        ("config_change", [DEPLOY, LOG], {"dep-1": "supports", "log-1": "supports"}, "wrong kind"),
        ("bad_deploy", [OTHER, LOG], {"dep-3": "supports", "log-1": "supports"}, "other service"),
        ("bad_deploy", [DEPLOY, LOG], {"dep-1": "symptom", "log-1": "supports"}, "not verified"),
        ("bad_deploy", [DEPLOY, LOG], {"dep-1": "irrelevant", "log-1": "supports"}, "not verified"),
    ],
)
def test_change_cause_without_its_evidence_gets_no_support(category, rows, labels, why):
    result = assess_hypothesis(
        hypothesis(category, supporting=list(labels)), labels, ledger(*rows), []
    )
    assert result.net_support == 0, why
    assert result.confidence == "low"
    assert "no verified" in result.disqualified


@pytest.mark.parametrize(
    "category",
    ["resource_exhaustion", "dependency_failure", "traffic_surge", "expired_certificate"],
)
def test_other_categories_do_not_need_a_change(category):
    metric = row("met-1", "metrics", service="pay", metric="cpu")
    result = assess_hypothesis(
        hypothesis(category, supporting=["log-1", "met-1"]),
        {"log-1": "supports", "met-1": "supports"},
        ledger(LOG, metric),
        [],
    )
    assert (result.disqualified, result.net_support, result.confidence) == ("", 2, "high")


def verdict(hypothesis_id, *rows):
    labels = [RowVerdict(evidence_id=r, label="supports", reason="r") for r in rows]
    return HypothesisVerdict(hypothesis_id=hypothesis_id, rows=labels)


def test_unevidenced_change_rival_no_longer_blocks_the_real_cause():
    # The INC-11 failure. Before: both net 2, lead 0 => blocked. After: rival net 0, lead 2.
    metric = row("met-1", "metrics", service="pay", metric="memory")
    data = ledger(LOG, metric)
    real = hypothesis("resource_exhaustion", supporting=["log-1", "met-1"], id="real")
    rival = hypothesis("config_change", supporting=["log-1", "met-1"], id="rival")
    verification = Verification(
        verdicts=[verdict("real", "log-1", "met-1"), verdict("rival", "log-1", "met-1")]
    )
    ranked = rank_hypotheses([rival, real], verification, data, [])
    assert [h.id for h in ranked] == ["real", "rival"]
    assert [h.net_support for h in ranked] == [2, 0]
    assert decide_outcome(ranked, ACCEPT) == ("conclusive", [])


def ranked(id, category, component, net, confidence="high"):
    base = hypothesis(category, component, id=id).model_dump()
    return RankedHypothesis(**base, net_support=net, confidence=confidence)


def test_unknown_is_not_a_rival():
    top = ranked("a", "resource_exhaustion", "pay", 2)
    assert decide_outcome([top, ranked("b", "unknown", "pay", 2)], ACCEPT)[0] == "conclusive"


def test_rewording_of_the_same_cause_is_not_a_rival():
    top = ranked("a", "resource_exhaustion", "pay", 2)
    twin = ranked("b", "resource_exhaustion", "pay", 2)
    assert decide_outcome([top, twin], ACCEPT)[0] == "conclusive"


@pytest.mark.parametrize(
    "rival",
    [
        ranked("b", "traffic_surge", "pay", 2),
        ranked("b", "resource_exhaustion", "db", 2),
    ],
    ids=["same component, other category", "same category, other component"],
)
def test_real_rivals_still_block(rival):
    top = ranked("a", "resource_exhaustion", "pay", 2)
    outcome, reasons = decide_outcome([top, rival], ACCEPT)
    assert outcome == "inconclusive" and "lead" in reasons[0]


def test_lead_is_measured_against_the_first_real_rival():
    top = ranked("a", "resource_exhaustion", "pay", 3)
    unknown = ranked("u", "unknown", "pay", 3)
    real = ranked("r", "traffic_surge", "pay", 3)
    outcome, reasons = decide_outcome([top, unknown, real], ACCEPT)
    assert outcome == "inconclusive" and "'r'" in reasons[0]


# --- Fix 2: a rollback that did not help ---

ROLLBACK = row("dep-9", "deployments", ts="2026-01-01T10:20", service="pay", kind="rollback")


def errors(values, metric="error_rate", service="pay", start="2026-01-01T10:00", step=10):
    return row(
        "met-1",
        "metrics",
        service=service,
        metric=metric,
        start=start,
        step_min=step,
        values=values,
    )


def assess_deploy(metric, rollback=ROLLBACK):
    rows = [r for r in (DEPLOY, LOG, metric, rollback) if r]
    labels = {"dep-1": "supports", "log-1": "supports"}
    return assess_hypothesis(hypothesis(supporting=list(labels)), labels, ledger(*rows), [])


# Times are 10:00, 10:10, 10:20 | 10:30, 10:40. Rollback at 10:20 belongs to "before".
@pytest.mark.parametrize(
    ("values", "worked_out", "disqualified"),
    [
        ([0.0, 0.2, 0.2, 0.2, 0.2], "peak 0.2, after mean 0.2, limit 0.1", True),
        ([0.0, 0.2, 0.2, 0.1, 0.1], "after mean 0.1 equals limit 0.1, so not recovered", True),
        ([0.0, 0.2, 0.2, 0.09, 0.09], "after mean 0.09 is under limit 0.1", False),
        ([0.0, 0.2, 0.2, 0.0, 0.0], "fully recovered", False),
        ([0.0, 0.2, 0.2, 0.3, 0.3], "errors got worse", True),
    ],
)
def test_rollback_rule_calculation(values, worked_out, disqualified):
    result = assess_deploy(errors(values))
    assert bool(result.disqualified) is disqualified, worked_out
    assert result.confidence == ("low" if disqualified else "high"), worked_out


def test_failed_rollback_is_listed_as_contradicting_and_removes_all_support():
    result = assess_deploy(errors([0.0, 0.2, 0.2, 0.2, 0.2]))
    assert result.verified_contradicting == ["dep-9"]
    assert result.net_support == -1  # 0 counted supporting rows - 1 contradicting
    assert "dep-9 rolled it back" in result.disqualified and "met-1" in result.disqualified


@pytest.mark.parametrize(
    ("metric", "rollback", "why"),
    [
        (errors([0.2] * 5), None, "no rollback happened"),
        (None, ROLLBACK, "no error metric was retrieved"),
        (errors([0.2] * 5, metric="cpu"), ROLLBACK, "not an error metric"),
        (errors([0.2] * 5, service="db"), ROLLBACK, "metric from another service"),
        (errors([0.2, 0.2, 0.2], step=5), ROLLBACK, "no data after the rollback"),
        (errors([0.2, 0.2], start="2026-01-01T11:00"), ROLLBACK, "no data before the rollback"),
        (errors([0.0, 0.0, 0.0, 0.0, 0.0]), ROLLBACK, "no errors before or after"),
        (errors([0.0, 0.0, 0.0, 0.3, 0.3]), ROLLBACK, "errors started only after the rollback"),
        (errors([0.0, 0.0, 0.0]), ROLLBACK, "no errors, and no data after"),
        (
            errors([0.2] * 5),
            row("dep-9", "deployments", ts="2026-01-01T10:20", service="db", kind="rollback"),
            "rollback of another service",
        ),
    ],
)
def test_rollback_rule_does_not_fire_without_real_evidence(metric, rollback, why):
    assert assess_deploy(metric, rollback).disqualified == "", why


def test_earliest_rollback_is_used():
    # From 10:10: before peak 0.2, after mean 0.2 => not recovered, even if a later one looks fine.
    early = row("dep-8", "deployments", ts="2026-01-01T10:10", service="pay", kind="rollback")
    metric = errors([0.2, 0.2, 0.2, 0.2, 0.2])
    rows = ledger(DEPLOY, LOG, metric, ROLLBACK, early)
    labels = {"dep-1": "supports", "log-1": "supports"}
    result = assess_hypothesis(hypothesis(supporting=list(labels)), labels, rows, [])
    assert "dep-8" in result.disqualified


def test_rollback_rule_applies_only_to_change_causes():
    metric = errors([0.0, 0.2, 0.2, 0.2, 0.2])
    labels = {"log-1": "supports", "met-1": "supports"}
    result = assess_hypothesis(
        hypothesis("resource_exhaustion", supporting=list(labels)),
        labels,
        ledger(LOG, metric, ROLLBACK),
        [],
    )
    assert (result.disqualified, result.verified_contradicting) == ("", [])


def test_inc12_failure_now_picks_the_real_cause():
    # Before: deploy net 4, postgres net 3, lead 1 => wrongly concluded the deploy.
    metric = errors([0.003, 0.21, 0.22, 0.21, 0.22])
    disk = row("met-3", "metrics", service="postgres", metric="disk")
    pg_log = row("log-3", "logs", service="postgres", msg="no space")
    log_2 = row("log-2", "logs", service="pay", msg="write failed")
    data = ledger(DEPLOY, LOG, log_2, metric, ROLLBACK, disk, pg_log)
    deploy = hypothesis(supporting=["dep-1", "log-1", "log-2", "met-1"], id="deploy")
    disk_full = hypothesis(
        "resource_exhaustion", "postgres", ["log-3", "met-3", "log-1"], id="disk"
    )
    verification = Verification(
        verdicts=[
            verdict("deploy", "dep-1", "log-1", "log-2", "met-1"),
            verdict("disk", "log-3", "met-3", "log-1"),
        ]
    )
    result = rank_hypotheses([deploy, disk_full], verification, data, [])
    assert result[0].id == "disk" and result[0].net_support == 3
    assert result[1].net_support == -1 and result[1].disqualified
    assert decide_outcome(result, ACCEPT) == ("conclusive", [])


def test_labels_are_kept_for_diagnosis():
    labels = {"dep-1": "supports", "log-1": "symptom"}
    result = assess_hypothesis(hypothesis(supporting=["dep-1"]), labels, ledger(DEPLOY, LOG), [])
    assert result.row_labels == labels
