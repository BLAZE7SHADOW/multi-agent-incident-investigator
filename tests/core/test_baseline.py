from fakes import RoutingChatModel, reply

from investigator.baseline import investigate_baseline
from investigator.budget import RunBudget
from investigator.schemas import Incident, Limits, World
from investigator.tools import build_tools

INCIDENT = Incident(id="T-1", title="t", description="d", service="api", reported_at="2026-01-01")
WORLD = World(
    logs=[
        {"id": "log-1", "ts": "2026-01-01T10:00", "service": "api", "level": "ERROR", "msg": "x"}
    ],
    deployments=[
        {
            "id": "dep-1",
            "ts": "2026-01-01T09:59",
            "service": "api",
            "kind": "deploy",
            "detail": "v2",
        }
    ],
)
HYPOTHESIS = {
    "id": "h1",
    "statement": "bad deploy",
    "category": "bad_deploy",
    "component": "api",
    "supporting": ["log-1", "dep-1"],
    "contradicting": [],
    "assumptions": ["traffic normal"],
}


def output(verdict="accept", labels=("log-1", "dep-1"), **overrides):
    rows = [{"evidence_id": row, "label": "supports", "reason": "r"} for row in labels]
    return {
        "claims": [
            {"text": "deploy happened", "kind": "fact", "evidence_ids": ["dep-1"]},
            {"text": "invented", "kind": "fact", "evidence_ids": ["fake"]},
        ],
        "gaps": [],
        "hypotheses": [HYPOTHESIS],
        "verification": [{"hypothesis_id": "h1", "rows": rows}],
        "verdict": verdict,
        "summary": "it was the deploy",
        "actions": [{"description": "Add alert", "risk": "low", "hypothesis_id": "h1"}],
        **overrides,
    }


def scripted(*final):
    return RoutingChatModel(
        replies={
            "Incident": [
                reply("query_logs", {"service": "api"}),
                reply("get_deployments", {"service": "api"}),
                *final,
            ]
        }
    )


async def run(model, world=WORLD, limits=None, **kwargs):
    return await investigate_baseline(
        INCIDENT, model, build_tools(world), RunBudget(limits), **kwargs
    )


async def test_baseline_uses_the_same_rules_and_report_format():
    report = await run(scripted(reply("BaselineOutput", output())))
    assert report.outcome == "conclusive" and report.stop_reason == "agent_accept"
    assert report.hypotheses[0].confidence == "high"
    assert [c.text for c in report.facts] == ["deploy happened"]
    assert "invented" in [c.text for c in report.assumptions]
    assert report.grounding.downgraded_claims == 1
    assert report.grounding.validity < 1
    assert report.summary == "it was the deploy"
    assert report.usage.tool_calls == 2 and report.usage.llm_calls == 3


async def test_baseline_has_all_tools_and_one_agent():
    model = scripted(reply("BaselineOutput", output()))
    report = await run(model)
    assert set(report.usage_by_agent) == {"baseline"}
    assert {"query_logs", "get_deployments", "search_incidents"} <= set(model.bound[0])


async def test_its_own_insufficient_verdict_means_inconclusive():
    report = await run(scripted(reply("BaselineOutput", output("insufficient"))))
    assert report.outcome == "inconclusive"
    assert report.stop_reason == "agent_insufficient"


async def test_rules_override_its_own_acceptance():
    report = await run(scripted(reply("BaselineOutput", output(labels=("log-1",)))))
    assert report.outcome == "inconclusive"
    assert report.hypotheses[0].confidence == "low"


async def test_invented_rows_cannot_create_support():
    report = await run(scripted(reply("BaselineOutput", output(labels=("fake-a", "fake-b")))))
    assert report.hypotheses[0].verified_supporting == []
    assert report.outcome == "inconclusive"


class Boom(Exception):
    status_code = 401


async def test_failure_still_produces_an_inconclusive_report():
    report = await run(RoutingChatModel(replies={"Incident": [Boom("no")]}))
    assert report.outcome == "inconclusive"
    assert report.stop_reason == "baseline_failed"
    assert any("Boom" in gap for gap in report.gaps)


class Quota(Exception):
    def __str__(self):
        return "RequestsPerDay exceeded"


async def test_quota_failure_is_reported_as_such():
    report = await run(RoutingChatModel(replies={"Incident": [Quota()]}))
    assert report.stop_reason == "provider_quota_exhausted"


async def test_step_limit_keeps_going_to_a_report():
    many = [reply("query_logs", {"service": f"s{i}"}) for i in range(20)]
    report = await run(RoutingChatModel(replies={"Incident": many}))
    assert report.outcome == "inconclusive"
    assert report.stop_reason == "step_limit_reached"
    assert report.usage.llm_calls <= 12


async def test_tool_budget_is_enforced():
    many = [reply("query_logs", {"service": f"s{i}"}) for i in range(5)]
    final = reply("BaselineOutput", output())
    limits = Limits(max_tool_calls=2)
    report = await run(RoutingChatModel(replies={"Incident": [*many, final]}), limits=limits)
    assert report.usage.tool_calls <= 2


async def test_action_policy_applies():
    calls = []

    def policy(actions):
        calls.append(actions)
        return actions

    await run(scripted(reply("BaselineOutput", output())), action_policy=policy)
    assert calls
