import json

import pytest

from evals.scoring import RunScore
from investigator.tools import load_cases
from reliability.audit import audit, audit_run
from reliability.rule_eval import confusion, evaluate, render

RESULTS = evaluate()
BY_NAME = {r["name"]: r for r in RESULTS}


def test_every_scenario_except_the_known_gap_is_now_correct():
    failing = [r["name"] for r in RESULTS if not r["correct"]]
    assert failing == [r["name"] for r in RESULTS if r["known_gap"]]


@pytest.mark.parametrize(
    ("name", "top"),
    [
        ("INC-11, unevidenced config rival ties the real cause", "resource_exhaustion/inventory"),
        ('INC-11, "unknown" rival ties the real cause', "resource_exhaustion/inventory"),
        (
            "INC-09, unevidenced config rival with 6 rows ties the real cause",
            "resource_exhaustion/auth",
        ),
        ("INC-12, rolled-back deploy beats the real cause by 1", "resource_exhaustion/postgres"),
        (
            "INC-12, rolled-back deploy beats the real cause by 1, smaller",
            "resource_exhaustion/postgres",
        ),
    ],
)
def test_each_reconstructed_failure_is_fixed(name, top):
    result = BY_NAME[name]
    assert (result["outcome"], result["top"]) == ("conclusive", top)


def test_known_gap_is_still_a_wrong_conclusion():
    gap = next(r for r in RESULTS if r["known_gap"])
    assert gap["outcome"] == "conclusive" and not gap["correct"]


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
    # Should conclude (10): 5 reconstructed failures + 5 controls (INC-01, 02, 05, 03, 12).
    # All 10 do: TP 10.
    # Should not (3): INC-08 and INC-10 "insufficient" stay inconclusive: TN 2.
    # The INC-10 known gap concludes when it should not: FP 1. No right cause is missed: FN 0.
    stats = confusion(RESULTS)
    assert sum(r["expected"]["outcome"] == "conclusive" for r in RESULTS) == 10
    assert (stats["tp"], stats["fp"], stats["fn"], stats["tn"]) == (10, 1, 0, 2)
    assert stats["precision"] == 10 / 11 and stats["recall"] == 1.0


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
