import json

import pytest

from reliability.metamorphic import (
    TRANSFORMS,
    check,
    investigate_offline,
    load_raw,
    render,
    run_suite,
)

TOPOLOGY, CASES = load_raw()
CASE = {c["incident"]["id"]: c for c in CASES}
RESULTS = run_suite()


def text(*parts):
    return json.dumps(parts, default=str)


def test_suite_covers_every_conclusive_case_and_transform():
    assert len(CASES) == 10
    assert len(RESULTS) == len(CASES) * len(TRANSFORMS)


@pytest.mark.parametrize("raw", CASES, ids=lambda c: c["incident"]["id"])
def test_every_original_case_is_solved_before_any_change(raw):
    result = investigate_offline(raw, TOPOLOGY)
    truth = raw["truth"]
    assert result["outcome"] == "conclusive"
    assert (result["category"], result["component"]) == (truth["category"], truth["component"])
    assert result["retrieved_key"] == sorted(truth["key_evidence"])


@pytest.mark.parametrize("name", list(TRANSFORMS))
def test_every_transform_changes_the_data_and_leaves_the_original_alone(name):
    raw = CASE["INC-02"]
    before = text(raw, TOPOLOGY)
    changed, topology, _, _ = TRANSFORMS[name][0](raw, TOPOLOGY)
    assert text(changed, topology) != before
    assert text(raw, TOPOLOGY) == before


def test_service_rename_reaches_every_field():
    changed, topology, renames, _ = TRANSFORMS["rename services"][0](CASE["INC-02"], TOPOLOGY)
    assert changed["truth"]["component"] == "inventory-api"
    assert changed["incident"]["service"] == "checkout-api"
    blob = text(changed["world"], changed["incident"], topology)
    for old in renames:
        assert f'"{old}"' not in blob
    assert "timeout calling inventory-api" in blob


def test_row_id_rename_keeps_key_evidence_pointing_at_the_same_rows():
    raw = CASE["INC-01"]
    changed, *_ = TRANSFORMS["rename row IDs"][0](raw, TOPOLOGY)
    rows = {
        r["id"]: r for t in ("logs", "metrics", "deployments") for r in changed["world"].get(t, [])
    }
    old = {r["id"]: r for t in ("logs", "metrics", "deployments") for r in raw["world"].get(t, [])}
    for before, after in zip(
        raw["truth"]["key_evidence"], changed["truth"]["key_evidence"], strict=True
    ):
        assert {k: v for k, v in rows[after].items() if k != "id"} == {
            k: v for k, v in old[before].items() if k != "id"
        }


def test_time_shift_moves_every_timestamp():
    changed, *_ = TRANSFORMS["shift time by 400 days"][0](CASE["INC-12"], TOPOLOGY)
    assert "2026-03-21" not in text(changed["world"], changed["incident"])
    assert changed["world"]["metrics"][0]["start"].startswith("2027-04-25")


@pytest.mark.parametrize(("name", "rows"), [("add 3 noise rows", 3), ("add 25 noise rows", 25)])
def test_noise_adds_exactly_the_stated_rows(name, rows):
    raw = CASE["INC-01"]
    changed, *_ = TRANSFORMS[name][0](raw, TOPOLOGY)
    assert len(changed["world"]["logs"]) - len(raw["world"]["logs"]) == rows


def outcome(**fields):
    base = {"outcome": "conclusive", "category": "c", "component": "x", "confidence": "high"}
    return {**base, **fields}


@pytest.mark.parametrize(
    ("changed", "relation", "renames", "passes"),
    [
        (outcome(), "same", {}, True),
        (outcome(component="x-api"), "same", {"x": "x-api"}, True),
        (outcome(component="x"), "same", {"x": "x-api"}, False),
        (outcome(confidence="medium"), "same", {}, False),
        (outcome(confidence="medium"), "not_more", {}, True),
        (outcome(outcome="inconclusive", confidence="low"), "not_more", {}, True),
    ],
)
def test_check_relations(changed, relation, renames, passes):
    assert check(outcome(), changed, relation, renames)[0] is passes


def test_confidence_rising_fails_not_more():
    original = outcome(outcome="inconclusive", confidence="low")
    assert check(original, outcome(confidence="medium"), "not_more", {})[0] is False


def test_current_results_are_recorded():
    """Today's results. A change that moves these must be explained."""
    failures = sorted((r["incident"], r["transform"]) for r in RESULTS if not r["passed"])
    assert failures == [
        ("INC-04", "add 25 noise rows"),
        ("INC-06", "add 25 noise rows"),
        ("INC-07", "add 25 noise rows"),
        ("INC-11", "add 25 noise rows"),
    ]


def test_report_lists_failures():
    text_out = render(RESULTS)
    assert "**106/110**" in text_out and "INC-04 | add 25 noise rows" in text_out
