import pytest
from pydantic import TypeAdapter

from evals.__main__ import render
from evals.scoring import RunScore, score_run, summarize
from investigator.schemas import Case, Grounding, RankedHypothesis, Report, Usage

CONCLUSIVE = Case.model_validate(
    {
        "incident": {
            "id": "X-1",
            "title": "t",
            "description": "d",
            "service": "api",
            "reported_at": "2026-01-01",
        },
        "scenario": "s",
        "split": "holdout",
        "world": {"logs": [{"id": "a"}, {"id": "b"}, {"id": "c"}], "deployments": [{"id": "d"}]},
        "truth": {
            "outcome": "conclusive",
            "category": "bad_deploy",
            "component": "api",
            "key_evidence": ["a", "b"],
            "trap_component": "db",
        },
    }
)
INCONCLUSIVE = CONCLUSIVE.model_copy(
    update={
        "truth": CONCLUSIVE.truth.model_copy(
            update={
                "outcome": "inconclusive",
                "category": "unknown",
                "component": "",
                "key_evidence": [],
                "trap_component": "",
            }
        )
    }
)


def hypothesis(category="bad_deploy", component="api", supporting=(), verified=()):
    return RankedHypothesis(
        id="h1",
        statement="s",
        category=category,
        component=component,
        supporting=list(supporting),
        contradicting=[],
        assumptions=[],
        verified_supporting=list(verified),
        confidence="high",
    )


def report(outcome="conclusive", hypotheses=None, stop="critic_accepted", grounding=None):
    return Report(
        incident_id="X-1",
        outcome=outcome,
        stop_reason=stop,
        summary="s",
        hypotheses=[hypothesis()] if hypotheses is None else hypotheses,
        facts=[],
        assumptions=[],
        gaps=["g"],
        actions=[],
        usage=Usage(input_tokens=7, output_tokens=3, thinking_tokens=1, llm_calls=2, tool_calls=4),
        grounding=grounding or Grounding(citations=4, valid_citations=3),
    )


def score(case=CONCLUSIVE, rep=None, system="multi_agent", latency=2.0):
    return score_run(case, rep or report(), system, 0, latency)


def test_correct_needs_conclusion_category_and_component():
    assert score().correct is True


@pytest.mark.parametrize(
    "top",
    [hypothesis(category="config_change"), hypothesis(component="db")],
    ids=["wrong category", "wrong component"],
)
def test_wrong_root_cause_is_a_wrong_conclusion(top):
    result = score(rep=report(hypotheses=[top]))
    assert result.correct is False and result.wrong_conclusion is True


def test_right_cause_without_concluding_is_missed_not_correct():
    result = score(rep=report(outcome="inconclusive"))
    assert (result.correct, result.missed_conclusion, result.wrong_conclusion) == (
        False,
        True,
        False,
    )


def test_inconclusive_case_is_correct_only_when_inconclusive():
    assert score(INCONCLUSIVE, report(outcome="inconclusive")).correct is True
    concluded = score(INCONCLUSIVE, report(outcome="conclusive"))
    assert concluded.correct is False and concluded.wrong_conclusion is True


def test_runner_up_is_recorded_to_explain_failures():
    rival = hypothesis(category="config_change", component="db")
    assert score(rep=report(hypotheses=[hypothesis(), rival])).runner_up == "config_change/db net=0"
    assert score().runner_up == ""


def test_no_hypotheses_is_handled():
    result = score(rep=report(outcome="inconclusive", hypotheses=[]))
    assert result.correct is False and result.evidence_recall is None


def test_falling_for_the_trap_is_recorded_only_when_concluded():
    trap = hypothesis(component="db")
    assert score(rep=report(hypotheses=[trap])).fell_for_trap is True
    assert score(rep=report(outcome="inconclusive", hypotheses=[trap])).fell_for_trap is False
    assert score(rep=report()).fell_for_trap is False


def test_evidence_precision_and_recall_are_exact():
    top = hypothesis(supporting=["a", "c"], verified=["a", "d"])
    result = score(rep=report(hypotheses=[top]))
    assert result.evidence_recall == 0.5  # found a of a, b
    assert result.evidence_precision == pytest.approx(1 / 3)  # a of a, c, d


def test_no_key_evidence_means_no_precision_or_recall():
    result = score(INCONCLUSIVE, report(outcome="inconclusive"))
    assert result.evidence_recall is None and result.evidence_precision is None


def test_usage_and_grounding_are_copied():
    result = score(latency=9.5)
    assert (result.total_tokens, result.thinking_tokens, result.tool_calls) == (10, 1, 4)
    assert result.llm_calls == 2 and result.latency_s == 9.5
    assert result.citation_validity == 0.75


def test_quota_exhausted_runs_are_invalid_and_never_averaged():
    bad = score(INCONCLUSIVE, report(outcome="inconclusive", stop="provider_quota_exhausted"))
    assert bad.valid is False and bad.correct is True
    good = score(rep=report(outcome="inconclusive"))
    summary = summarize([bad, good])
    assert summary["runs"] == 1 and summary["invalid_runs"] == 1
    assert summary["accuracy"] == 0


def test_summary_averages_and_skips_missing_values():
    first = score()
    second = score(INCONCLUSIVE, report(outcome="conclusive"))
    summary = summarize([first, second])
    assert summary["accuracy"] == 0.5
    assert summary["wrong_conclusion_rate"] == 0.5
    assert summary["total_tokens"] == 10


def test_all_invalid_runs_do_not_crash_the_summary_or_report():
    crashed = RunScore(
        incident_id="X",
        scenario="s",
        split="dev",
        system="baseline",
        repeat=0,
        valid=False,
        error="Boom",
    )
    assert summarize([crashed]) == {"runs": 0, "invalid_runs": 1}
    assert "n/a" in render([crashed], "m")


def test_report_compares_both_systems_and_splits():
    scores = [score(), score(system="baseline", rep=report(outcome="inconclusive"))]
    text = render(scores, "gemini:x")
    assert "gemini:x" in text and "| multi_agent | baseline |" in text
    assert "| Root-cause accuracy | 100% | 0% |" in text
    assert "| holdout | 100% | 0% |" in text
    assert "| X-1 | s | 1/1 | 0/1 |" in text


def test_scores_survive_json():
    adapter = TypeAdapter(RunScore)
    assert adapter.validate_json(adapter.dump_json(score())) == score()
