import json

from evals.export_web import OUT, build, render


def test_viewer_data_has_every_part_the_pages_need():
    data = build()
    assert [e["id"] for e in data["evaluations"]] == ["v1", "v2"]
    assert {e["id"]: len(e["runs"]) for e in data["evaluations"]} == {"v1": 72, "v2": 48}
    assert len(data["incidents"]) == 36
    assert {c["set"] for c in data["incidents"]} == {"assignment", "blind"}
    assert len(data["rule_eval"]["scenarios"]) == 21
    assert len(data["metamorphic"]["assignment"]) == 130
    assert len(data["metamorphic"]["blind"]) == 234


def test_viewer_numbers_match_the_reported_results():
    v1, v2 = build()["evaluations"]
    assert round(v1["summary"]["multi_agent"]["accuracy"], 2) == 0.69
    assert round(v1["summary"]["baseline"]["accuracy"], 2) == 0.89
    assert round(v2["summary"]["multi_agent"]["accuracy"], 2) == 0.75
    assert round(v2["summary"]["baseline"]["accuracy"], 2) == 0.71


def test_committed_viewer_data_is_up_to_date():
    assert OUT.read_text() == render(build())
    assert json.loads(OUT.read_text())["incidents"]
