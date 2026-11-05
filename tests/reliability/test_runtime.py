from fakes import RoutingChatModel, reply

from cli import render
from investigator.tools import load_cases
from reliability.runtime import run_incident

CASE = next(c for c in load_cases() if c.incident.id == "INC-11")
SECRET = "sk-live-FAKE000TESTKEY"
EMAIL = "ops.oncall@example.com"
FINDING = {"summary": "s", "claims": [], "gaps": []}
HYPOTHESIS = {
    "id": "h1",
    "statement": "memory leak",
    "category": "resource_exhaustion",
    "component": "inventory",
    "supporting": ["log-01", "met-01"],
    "contradicting": [],
    "assumptions": [],
}
RISKY = {"description": "Disable auth", "risk": "low", "hypothesis_id": "h1"}


def task(id, agent):
    return {"id": id, "agent": agent, "question": "q", "depends_on": []}


def model():
    rows = [
        {"evidence_id": row, "label": "supports", "reason": "r"} for row in ("log-01", "met-01")
    ]
    return RoutingChatModel(
        replies={
            "Orchestrator": [reply("Plan", {"tasks": [task("o", "observability")]})],
            "Observability": [
                reply("query_logs", {"service": "inventory"}),
                reply("query_metrics", {"service": "inventory"}),
                reply("Finding", FINDING),
            ],
            "Reasoning": [reply("HypothesisSet", {"hypotheses": [HYPOTHESIS]})],
            "Verifier": [
                reply("Verification", {"verdicts": [{"hypothesis_id": "h1", "rows": rows}]})
            ],
            "Critic": [reply("Critique", {"verdict": "accept", "objections": [], "requests": []})],
            "Reporting": [reply("ReportDraft", {"summary": "done", "actions": [RISKY]})],
        }
    )


async def test_default_run_never_sends_secrets_to_any_agent():
    fake = model()
    await run_incident(CASE.incident, CASE.world, model=fake)
    assert len({name for name, _ in fake.calls}) == 6
    for name, messages in fake.calls:
        sent = str(messages)
        assert SECRET not in sent, f"secret reached {name}"
        assert EMAIL not in sent, f"email reached {name}"


async def test_default_run_flags_risky_actions():
    report = await run_incident(CASE.incident, CASE.world, model=model())
    assert report.actions[0].requires_human_approval is True


async def test_report_renders_for_a_person():
    report = await run_incident(CASE.incident, CASE.world, model=model())
    text = render(report)
    assert "CONCLUSIVE" in text and "memory leak" in text and "[needs approval]" in text
    assert SECRET not in text
