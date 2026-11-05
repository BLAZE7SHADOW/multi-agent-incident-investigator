import json

import pytest

from evals.scoring import RunScore
from investigator.tools import load_cases
from reliability.audit import audit, audit_run
from reliability.rule_eval import confusion, evaluate, render

RESULTS = evaluate()
BY_NAME = {r["name"]: r for r in RESULTS}


def by_set(name):
    group = [r for r in RESULTS if r["set"] == name]
    return sum(r["correct"] for r in group), len(group)


def test_scenario_sets_are_complete():
    assert by_set("reconstructed")[1] == 5
    assert by_set("control")[1] == 8
    assert by_set("adversarial")[1] == 8


def test_current_rules_by_set():
    """A record of today's rules. A change that moves these numbers must be explained."""
    assert by_set("reconstructed") == (0, 5)  # every rebuilt evaluation failure still fails
    assert by_set("control") == (6, 8)  # fails INC-08 deploy guess and the old INC-10 deploy
    assert by_set("adversarial") == (6, 8)  # fails the deploy-tool-down and http_5xx cases


@pytest.mark.parametrize(
    "name",
    [
        "Deploy logged under another name (payments-api)",
        "Change was a feature flag",
        "Two deploys, and the wrong one was rolled back",
        "Only one region was rolled back",
    ],
)
def test_cases_the_removed_fixes_broke_are_handled(name):
    assert BY_NAME[name]["correct"]


def test_confusion_counts_by_hand():
    def r(expected, outcome, correct):
        return {"expected": {"outcome": expected}, "outcome": outcome, "correct": correct}

    results = [
        r("conclusive", "conclusive", True),  # TP
        r("conclusive", "conclusive", False),  # wrong cause: FP and FN
        r("conclusive", "inconclusive", False),  # FN
        r("inconclusive", "inconclusive", True),  # TN
        r("inconclusive", "conclusive", False),  # FP
    ]
    stats = confusion(results)
    assert (stats["tp"], stats["fp"], stats["fn"], stats["tn"]) == (1, 2, 2, 1)
    assert stats["precision"] == 1 / 3 and stats["recall"] == 1 / 3
    assert stats["accuracy"] == 2 / 5


def test_current_numbers_by_hand():
    # Should conclude (15): 5 reconstructed + 5 controls (INC-01, 02, 05, 03, 12) + 5 adversarial.
    #   TP 10: the 5 controls and 5 adversarial. FN 5: all 5 reconstructed.
    #   2 of those (INC-12) conclude the wrong cause, so they are also FP.
    # Should not conclude (6): INC-08, both INC-10, tool-down, http_5xx, unknown.
    #   FP 4: INC-08, INC-10 3-day deploy, tool-down, http_5xx. TN 2: INC-10 insufficient, unknown.
    stats = confusion(RESULTS)
    assert sum(r["expected"]["outcome"] == "conclusive" for r in RESULTS) == 15
    assert (stats["tp"], stats["fp"], stats["fn"], stats["tn"]) == (10, 6, 5, 2)
    assert stats["precision"] == 10 / 16 and stats["recall"] == 10 / 15


def test_report_shows_before_and_after():
    before = [{**r, "correct": False, "outcome": "inconclusive"} for r in RESULTS]
    text = render(RESULTS, before)
    assert "| Precision |" in text and "| Before | After |" in text


# --- Audit of saved results ---

CASES = {c.incident.id: c for c in load_cases()}


def run(**fields):
    base = {
        "incident_id": "INC-01",
        "scenario": "s",
        "split": "dev",
        "system": "m",
        "repeat": 0,
        "outcome": "conclusive",
        "top_category": "bad_deploy",
        "top_component": "payments",
        "correct": True,
        "expected_outcome": "conclusive",
    }
    return RunScore(**{**base, **fields})


def test_a_consistent_run_passes():
    assert audit_run(run(), CASES["INC-01"].truth) == []


@pytest.mark.parametrize(
    ("fields", "problem"),
    [
        ({"correct": False}, "correct"),
        ({"top_component": "checkout"}, "correct"),
        ({"wrong_conclusion": True}, "wrong_conclusion"),
        ({"total_tokens": 5}, "total_tokens"),
        ({"output_tokens": 1, "thinking_tokens": 2, "total_tokens": 1}, "thinking"),
        ({"evidence_recall": 1.5}, "outside"),
        (
            {"cited": ["dep-01"], "evidence_recall": 1.0, "evidence_precision": 1.0},
            "evidence_recall",
        ),
    ],
)
def test_tampered_values_are_caught(fields, problem):
    problems = audit_run(run(**fields), CASES["INC-01"].truth)
    assert any(problem in p for p in problems)


def test_saved_evaluation_passes_its_audit():
    from reliability.analysis import RESULTS as DIR
    from reliability.analysis import load_scores

    result = audit(
        load_scores(DIR / "results.json"),
        (DIR / "results.md").read_text(),
        "gemini:gemini-3.6-flash",
    )
    assert result["problems"] == [] and result["runs"] == 72


def test_audit_catches_a_report_that_was_edited(tmp_path):
    from reliability.analysis import RESULTS as DIR
    from reliability.analysis import load_scores

    text = (DIR / "results.md").read_text().replace("| 69% |", "| 99% |")
    result = audit(load_scores(DIR / "results.json"), text, "gemini:gemini-3.6-flash")
    assert any("results.md" in p for p in result["problems"])


def test_scores_round_trip():
    assert RunScore.model_validate(json.loads(run().model_dump_json())) == run()
