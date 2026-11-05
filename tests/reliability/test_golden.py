import pytest
import yaml
from fakes import RoutingChatModel, reply

from investigator.schemas import Report
from investigator.tools import load_cases
from reliability.golden import (
    GoldenError,
    add_case,
    draft_from_report,
    load_golden,
    validate_draft,
)
from reliability.runtime import run_incident

SECRET_CASE = next(c for c in load_cases() if c.incident.id == "INC-11")
FINDING = {"summary": "s", "claims": [], "gaps": []}


def draft(**overrides):
    raw = {
        "incident": {
            "id": "PROD-1",
            "title": "t",
            "description": "d",
            "service": "api",
            "reported_at": "2026-01-01",
        },
        "scenario": "disk filled up",
        "provenance": "production",
        "split": "dev",
        "reviewed_by": "on-call lead",
        "world": {"logs": [{"id": "log-1", "service": "api", "msg": "disk full"}]},
        "truth": {
            "outcome": "conclusive",
            "category": "resource_exhaustion",
            "component": "api",
            "key_evidence": ["log-1"],
        },
    }
    return {**raw, **overrides}


@pytest.fixture
def extra(tmp_path):
    path = tmp_path / "golden_extra.yaml"
    path.write_text("# keep this comment\ncases: []\n")
    return path


def test_golden_is_the_frozen_cases_plus_extras(extra, tmp_path):
    assert len(load_golden(extra)) == len(load_cases())
    source = tmp_path / "draft.yaml"
    source.write_text(yaml.safe_dump(draft()))
    add_case(source, extra)
    golden = load_golden(extra)
    assert len(golden) == len(load_cases()) + 1
    assert golden[-1].incident.id == "PROD-1"
    assert golden[-1].provenance == "production"
    assert golden[-1].world.dependencies  # shares the frozen topology
    assert "# keep this comment" in extra.read_text()


def test_frozen_assignment_cases_are_never_changed_by_adding(extra, tmp_path):
    before = load_cases()
    source = tmp_path / "draft.yaml"
    source.write_text(yaml.safe_dump(draft()))
    add_case(source, extra)
    assert load_cases() == before


def test_the_real_extra_file_starts_empty():
    assert len(load_golden()) == len(load_cases())


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"reviewed_by": ""}, "reviewed_by"),
        ({"reviewed_by": "   "}, "reviewed_by"),
        ({"scenario": ""}, "scenario"),
        ({"truth": {"outcome": "", "category": "", "key_evidence": []}}, "invalid case"),
        ({"truth": {"outcome": "conclusive", "category": "unknown"}}, "conclusive case needs"),
        (
            {"truth": {"outcome": "conclusive", "category": "bad_deploy", "component": "api"}},
            "conclusive case needs",
        ),
        (
            {
                "truth": {
                    "outcome": "inconclusive",
                    "category": "unknown",
                    "key_evidence": ["log-1"],
                }
            },
            "inconclusive case",
        ),
        (
            {
                "truth": {
                    "outcome": "conclusive",
                    "category": "bad_deploy",
                    "component": "api",
                    "key_evidence": ["log-99"],
                }
            },
            "log-99",
        ),
        (
            {"world": {"logs": [{"id": "log-1", "msg": "api_key=sk-live-abcdefgh12345"}]}},
            "redact",
        ),
        ({"world": {"logs": [{"id": "log-1", "msg": "owner a.person@example.com"}]}}, "redact"),
    ],
)
def test_unready_cases_are_rejected(overrides, message):
    with pytest.raises(GoldenError, match=message):
        validate_draft(draft(**overrides), set())


def test_secret_in_the_incident_text_is_rejected():
    incident = {**draft()["incident"], "description": "password=hunter2secret"}
    with pytest.raises(GoldenError, match="redact"):
        validate_draft(draft(incident=incident), set())


def test_duplicate_ids_are_rejected():
    with pytest.raises(GoldenError, match="already exists"):
        validate_draft(draft(), {"PROD-1"})


def test_valid_inconclusive_case_is_accepted():
    truth = {"outcome": "inconclusive", "category": "unknown"}
    assert validate_draft(draft(truth=truth), set()).truth.outcome == "inconclusive"


def test_failed_add_leaves_the_file_unchanged(extra, tmp_path):
    source = tmp_path / "draft.yaml"
    source.write_text(yaml.safe_dump(draft(reviewed_by="")))
    before = extra.read_text()
    with pytest.raises(GoldenError):
        add_case(source, extra)
    assert extra.read_text() == before


async def real_report():
    hypothesis = {
        "id": "h1",
        "statement": "memory leak",
        "category": "resource_exhaustion",
        "component": "inventory",
        "supporting": ["log-01"],
        "contradicting": [],
        "assumptions": [],
    }
    model = RoutingChatModel(
        replies={
            "Orchestrator": [
                reply(
                    "Plan",
                    {
                        "tasks": [
                            {"id": "o", "agent": "observability", "question": "q", "depends_on": []}
                        ]
                    },
                )
            ],
            "Observability": [
                reply("query_logs", {"service": "inventory"}),
                reply("Finding", FINDING),
            ],
            "Reasoning": [reply("HypothesisSet", {"hypotheses": [hypothesis]})],
            "Verifier": [reply("Verification", {"verdicts": []})],
            "Critic": [reply("Critique", {"verdict": "accept", "objections": [], "requests": []})],
            "Reporting": [reply("ReportDraft", {"summary": "s", "actions": []})],
        }
    )
    return await run_incident(SECRET_CASE.incident, SECRET_CASE.world, model=model)


async def test_draft_keeps_redacted_rows_and_never_copies_the_answer():
    report = await real_report()
    raw = draft_from_report(report)
    assert raw["incident"]["id"] == "INC-11"
    assert {row["id"] for row in raw["world"]["logs"]} == {"log-01", "log-02", "log-03"}
    assert "sk-live" not in str(raw) and "example.com" not in str(raw)
    assert raw["truth"]["category"] == "" and raw["truth"]["key_evidence"] == []
    assert raw["reviewed_by"] == ""
    assert "resource_exhaustion" not in str(raw)


async def test_unedited_draft_cannot_be_added():
    raw = draft_from_report(await real_report())
    with pytest.raises(GoldenError):
        validate_draft(raw, set())


async def test_report_survives_being_saved_and_loaded():
    report = await real_report()
    assert Report.model_validate_json(report.model_dump_json()) == report
