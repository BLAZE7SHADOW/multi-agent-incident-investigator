import json

import pytest

from evals.scoring import RunScore
from reliability.analysis import (
    calibration,
    calibration_finding,
    cost_per_run,
    load_scores,
    render,
    tokens_by_agent,
    wilson_interval,
)
from reliability.regression import BASELINE, RESULTS, compare, snapshot


def run(system="multi_agent", incident="A", correct=True, label="high", outcome="conclusive", **f):
    defaults = {"incident_id": incident, "scenario": "s", "split": "dev", "repeat": 0}
    return RunScore(
        **defaults,
        system=system,
        correct=correct,
        top_confidence=label,
        outcome=outcome,
        **f,
    )


def test_wilson_interval_known_values():
    low, high = wilson_interval(9, 11)
    assert low == pytest.approx(0.523, abs=0.005) and high == pytest.approx(0.949, abs=0.005)
    assert wilson_interval(0, 0) == (0.0, 1.0)
    assert wilson_interval(10, 10)[1] == 1.0


def test_calibration_counts_only_valid_conclusions():
    scores = [
        run(),
        run(correct=False),
        run(label="medium"),
        run(outcome="inconclusive"),
        run(valid=False),
    ]
    assert calibration(scores) == {"high": (1, 2), "medium": (1, 1), "low": (0, 0)}


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        ({"high": (9, 10), "medium": (5, 10)}, "were right more often"),
        ({"high": (9, 11), "medium": (10, 10)}, "right less often"),
        ({"high": (5, 5), "medium": (5, 5)}, "cannot separate"),
        ({"high": (0, 0), "medium": (1, 1)}, "Not enough"),
    ],
)
def test_calibration_finding(table, expected):
    assert expected in calibration_finding(table)


def test_cost_uses_the_prices_given_and_skips_invalid_runs():
    scores = [
        run(input_tokens=1_000_000, output_tokens=500_000),
        run(input_tokens=9, valid=False),
    ]
    assert cost_per_run(scores, 2.0, 4.0) == pytest.approx(4.0)
    assert cost_per_run([], 2.0, 4.0) == 0.0


def test_tokens_by_agent_averages_and_sorts():
    scores = [
        run(tokens_by_agent={"critic": 10, "verifier": 30}),
        run(tokens_by_agent={"critic": 20, "verifier": 50}),
    ]
    assert list(tokens_by_agent(scores).items()) == [("verifier", 40), ("critic", 15)]
    assert tokens_by_agent([run()]) == {}


def test_report_explains_missing_agent_data_and_optional_cost():
    text = render([run(input_tokens=1000)])
    assert "from the next run" in text and "Estimated cost" not in text
    assert "1/1" in text
    assert "Estimated cost" in render([run(input_tokens=1000)], 1.0, 1.0)


def test_saved_results_load_and_analyse_without_new_fields(tmp_path):
    path = tmp_path / "old.json"
    old = run().model_dump()
    del old["tokens_by_agent"]
    path.write_text(json.dumps([old]))
    assert len(load_scores(path)) == 1


# --- Regression gate ---


def scores(accuracy_runs=(True, True), tokens=100, latency=10.0, **fields):
    return [
        run(
            correct=correct,
            total_tokens=tokens,
            latency_s=latency,
            citation_validity=1.0,
            evidence_recall=0.8,
            **fields,
        )
        for correct in accuracy_runs
    ]


def check(before, after):
    return compare(snapshot(before), snapshot(after))


def test_identical_results_pass():
    assert check(scores(), scores()) == ([], [])


def test_accuracy_drop_fails():
    failures, _ = check(scores(), scores(accuracy_runs=(True, False)))
    assert any("accuracy" in failure for failure in failures)


def test_small_accuracy_change_within_tolerance_passes():
    before = scores(accuracy_runs=(True,) * 20)
    after = scores(accuracy_runs=(True,) * 19 + (False,))
    failures, warnings = check(before, after)
    assert failures == []
    assert any("accuracy fell" in warning for warning in warnings)


@pytest.mark.parametrize(
    ("change", "metric"),
    [
        ({"tokens": 121}, "total_tokens"),
        ({"latency": 13.1}, "latency_s"),
        ({"wrong_conclusion": True}, "wrong_conclusion_rate"),
        ({"fell_for_trap": True}, "trap_rate"),
    ],
)
def test_worse_results_fail(change, metric):
    failures, _ = check(scores(), scores(**change))
    assert any(metric in failure for failure in failures)


@pytest.mark.parametrize("change", [{"tokens": 120}, {"latency": 13.0}, {"tokens": 50}])
def test_results_at_or_under_the_limit_pass(change):
    assert check(scores(), scores(**change))[0] == []


def test_invalid_runs_fail():
    failures, _ = check(scores(), [*scores(), run(valid=False)])
    assert any("invalid" in failure for failure in failures)


def test_missing_system_or_incident_fails():
    assert check(scores(), [run(system="baseline")])[0]
    failures, _ = check(scores(), [run(incident="B", correct=True)])
    assert any("A was not run" in failure for failure in failures)


def test_saved_baseline_matches_the_saved_results():
    current = snapshot(load_scores(RESULTS / "results.json"))
    assert compare(json.loads(BASELINE.read_text()), current)[0] == []
