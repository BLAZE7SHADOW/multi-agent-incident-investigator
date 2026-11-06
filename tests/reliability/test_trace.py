import json

from fakes import RoutingChatModel, reply

from investigator.tools import load_cases
from reliability.runtime import run_incident
from reliability.trace import TraceRecorder, plain

CASE = next(c for c in load_cases() if c.incident.id == "INC-01")
FINDING = {"summary": "s", "claims": [], "gaps": []}
HYPOTHESIS = {
    "id": "h1",
    "statement": "bad deploy",
    "category": "bad_deploy",
    "component": "payments",
    "supporting": ["dep-01", "log-02"],
    "contradicting": [],
    "assumptions": [],
}


def model():
    rows = [{"evidence_id": r, "label": "supports", "reason": "r"} for r in ("dep-01", "log-02")]
    task = {"id": "o", "agent": "observability", "question": "q", "depends_on": []}
    dep = {"id": "d", "agent": "dependency", "question": "q", "depends_on": []}
    return RoutingChatModel(
        replies={
            "Orchestrator": [reply("Plan", {"tasks": [task, dep]})],
            "Observability": [
                reply("query_logs", {"service": "payments"}),
                reply("Finding", FINDING),
            ],
            "Dependency": [reply("get_deployments", {}), reply("Finding", FINDING)],
            "Reasoning": [reply("HypothesisSet", {"hypotheses": [HYPOTHESIS]})],
            "Verifier": [
                reply("Verification", {"verdicts": [{"hypothesis_id": "h1", "rows": rows}]})
            ],
            "Critic": [reply("Critique", {"verdict": "accept", "objections": [], "requests": []})],
            "Reporting": [reply("ReportDraft", {"summary": "done", "actions": []})],
        }
    )


async def recorded():
    recorder = TraceRecorder()
    report = await run_incident(
        CASE.incident, CASE.world, model=model(), on_step=recorder.step, callbacks=[recorder]
    )
    return recorder, report


async def test_every_workflow_step_is_recorded_in_order():
    recorder, report = await recorded()
    steps = [e["node"] for e in recorder.events if e["type"] == "step"]
    assert steps[0] == "orchestrate" and steps[-1] == "finalize"
    assert steps.count("run_task") == 2
    assert {"schedule", "reason", "verify", "criticize"} <= set(steps)
    assert report.outcome == "conclusive"


async def test_tool_calls_are_recorded_with_agent_and_rows():
    recorder, _ = await recorded()
    tools = [e for e in recorder.events if e["type"] == "tool"]
    assert {(t["tool"], t["agent"]) for t in tools} == {
        ("query_logs", "observability"),
        ("get_deployments", "dependency"),
    }
    logs = next(t for t in tools if t["tool"] == "query_logs")
    assert logs["status"] == "ok" and "log-02" in logs["rows"]


async def test_streaming_gives_the_same_report_as_a_plain_run():
    _, streamed = await recorded()
    plain_report = await run_incident(CASE.incident, CASE.world, model=model())
    assert streamed.outcome == plain_report.outcome
    assert [h.id for h in streamed.hypotheses] == [h.id for h in plain_report.hypotheses]


async def test_trace_is_plain_json():
    recorder, report = await recorded()
    assert json.loads(json.dumps({"events": recorder.events, "report": plain(report)}))
