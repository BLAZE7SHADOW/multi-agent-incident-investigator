import asyncio
import logging
from types import SimpleNamespace

import pytest
from fakes import ScriptedChatModel, reply
from langchain.agents import create_agent
from langchain_core.messages import ToolMessage
from pydantic import BaseModel

from investigator.budget import RunBudget
from investigator.evidence import collect_evidence
from investigator.graph import TaskOutcome, investigate
from investigator.llm import BudgetExhausted
from investigator.schemas import (
    Claim,
    Critique,
    EvidenceRequest,
    Fault,
    Finding,
    Hypothesis,
    HypothesisSet,
    HypothesisVerdict,
    Incident,
    Limits,
    Plan,
    ReportDraft,
    RowVerdict,
    TaskSpec,
    ToolResult,
    Usage,
    Verification,
    World,
)
from investigator.tools import build_tools, tool_retry, wrap_untrusted

INCIDENT = Incident(id="T-1", title="t", description="d", service="api", reported_at="2026-01-01")
OBS = TaskSpec(id="obs", agent="observability", question="q", depends_on=[])
DEP = TaskSpec(id="dep", agent="dependency", question="q", depends_on=[])
ACCEPT = Critique(verdict="accept", objections=[], requests=[])
MORE = Critique(
    verdict="investigate",
    objections=["why"],
    requests=[EvidenceRequest(agent="observability", question="more?", reason="r")],
)


def tool_message(tool, row_id):
    result = ToolResult(tool=tool, status="ok", rows=[{"id": row_id}])
    return ToolMessage(content=wrap_untrusted(result), tool_call_id="c1")


class StubAgents:
    """Configurable agents, so workflow behaviour can be tested with no LLM."""

    def __init__(self, plan=None, critiques=None, fail=None, delay=0.0, new_rows=True):
        self.plan_specs = [OBS, DEP] if plan is None else plan
        self.critiques = list(critiques or [ACCEPT])
        self.fail = fail or {}
        self.delay = delay
        self.new_rows = new_rows
        self.calls = {
            name: 0 for name in ("plan", "investigate", "reason", "verify", "criticize", "report")
        }
        self.events = []
        self.upstream = {}
        self.running = 0
        self.peak_running = 0

    def _enter(self, name):
        self.calls[name] += 1
        failure = self.fail.get(name)
        if failure is not None:
            raise failure

    async def plan(self, incident, critique, ledger):
        self._enter("plan")
        return Plan(tasks=self.plan_specs)

    async def investigate(self, task, incident, upstream):
        self.events.append(("start", task.id))
        self.running += 1
        self.peak_running = max(self.peak_running, self.running)
        await asyncio.sleep(self.delay)
        self.running -= 1
        self.upstream[task.id] = [f.summary for f in upstream]
        self._enter("investigate")
        failing = self.fail.get(task.id)
        if failing is not None:
            raise failing
        self.events.append(("end", task.id))
        source = "query_logs" if task.agent == "observability" else "get_deployments"
        suffix = task.id if self.new_rows else task.id.split("-", 1)[1]
        row = f"{source}-{suffix}"
        finding = Finding(
            summary=f"finding {task.id}",
            claims=[
                Claim(text=f"fact {task.id}", kind="fact", evidence_ids=[row]),
                Claim(text=f"made up {task.id}", kind="fact", evidence_ids=["fake"]),
            ],
            gaps=[],
        )
        return TaskOutcome(finding=finding, messages=[tool_message(source, row)])

    async def reason(self, incident, findings, ledger, gaps, critique):
        self._enter("reason")
        hypothesis = Hypothesis(
            id="h1",
            statement="the deploy broke it",
            category="bad_deploy",
            component="api",
            supporting=sorted(ledger),
            contradicting=[],
            assumptions=["traffic was normal"],
        )
        return HypothesisSet(hypotheses=[hypothesis])

    async def verify(self, hypotheses, ledger):
        self._enter("verify")
        rows = [RowVerdict(evidence_id=row, label="supports", reason="r") for row in ledger]
        return Verification(verdicts=[HypothesisVerdict(hypothesis_id="h1", rows=rows)])

    async def criticize(self, incident, ranked, ledger, gaps):
        self._enter("criticize")
        return self.critiques.pop(0) if self.critiques else ACCEPT

    async def report(self, incident, ranked, outcome, reasons, gaps):
        self._enter("report")
        return ReportDraft(summary="written by agent", actions=[])


async def run(agents, limits=None, **kwargs):
    return await investigate(INCIDENT, agents, RunBudget(limits or Limits()), **kwargs)


# --- Happy path and collaboration ---


async def test_complete_run_produces_a_conclusive_report():
    report = await run(StubAgents())
    assert (report.outcome, report.stop_reason) == ("conclusive", "critic_accepted")
    assert report.summary == "written by agent"
    assert report.hypotheses[0].confidence == "high"


async def test_report_separates_facts_from_assumptions():
    report = await run(StubAgents())
    assert {c.text for c in report.facts} == {"fact r1-obs", "fact r1-dep"}
    assumptions = {c.text for c in report.assumptions}
    assert {"made up r1-obs", "made up r1-dep", "traffic was normal"} == assumptions
    assert report.grounding.downgraded_claims == 2
    assert report.grounding.validity < 1


async def test_independent_tasks_run_in_parallel():
    agents = StubAgents(delay=0.05)
    await run(agents)
    assert agents.peak_running == 2


async def test_dependent_task_waits_and_receives_upstream_finding():
    second = TaskSpec(id="dep", agent="dependency", question="q", depends_on=["obs"])
    agents = StubAgents(plan=[OBS, second], delay=0.01)
    await run(agents)
    assert agents.events.index(("end", "r1-obs")) < agents.events.index(("start", "r1-dep"))
    assert agents.peak_running == 1
    assert agents.upstream["r1-dep"] == ["finding r1-obs"]


async def test_critic_feedback_starts_a_new_round_with_more_evidence():
    agents = StubAgents(critiques=[MORE, ACCEPT])
    report = await run(agents)
    assert agents.calls["plan"] == 2
    assert agents.calls["criticize"] == 2
    assert report.stop_reason == "critic_accepted"
    assert any("r2-obs" in row for row in report.hypotheses[0].supporting)


# --- Stop conditions ---


@pytest.mark.parametrize(
    ("critiques", "limits", "new_rows", "expected", "rounds"),
    [
        ([MORE] * 9, Limits(max_rounds=2), True, "max_rounds_reached", 2),
        ([MORE] * 9, Limits(), False, "no_new_evidence", 2),
        (
            [Critique(verdict="insufficient", objections=[], requests=[])],
            Limits(),
            True,
            "critic_insufficient",
            1,
        ),
        (
            [Critique(verdict="investigate", objections=[], requests=[])],
            Limits(),
            True,
            "critic_requested_nothing",
            1,
        ),
    ],
)
async def test_stop_conditions(critiques, limits, new_rows, expected, rounds):
    agents = StubAgents(critiques=critiques, new_rows=new_rows)
    report = await run(agents, limits)
    assert report.stop_reason == expected
    assert report.outcome == "inconclusive"
    assert agents.calls["plan"] == rounds


class SpendingAgents(StubAgents):
    def __init__(self, budget, spend, **kwargs):
        super().__init__(**kwargs)
        self.budget = budget
        self.spend = spend

    async def plan(self, incident, critique, ledger):
        self.budget.usage += self.spend
        return await super().plan(incident, critique, ledger)


@pytest.mark.parametrize(
    ("spend", "limits", "expected"),
    [
        (Usage(tool_calls=5), Limits(max_tool_calls=5), "tool_budget_exhausted"),
        (Usage(input_tokens=100), Limits(max_tokens=100), "token_budget_exhausted"),
        (Usage(), Limits(max_seconds=0), "deadline_exceeded"),
    ],
)
async def test_budget_exhaustion_stops_work_and_still_reports(spend, limits, expected):
    budget = RunBudget(limits)
    agents = SpendingAgents(budget, spend)
    report = await investigate(INCIDENT, agents, budget)
    assert report.stop_reason == expected
    assert report.outcome == "inconclusive"
    assert agents.calls["investigate"] == 0
    assert agents.calls["report"] == 0
    assert report.summary.startswith("Inconclusive")
    assert any(expected in gap for gap in report.gaps)


async def test_budget_spent_during_analysis_prevents_another_round():
    budget = RunBudget(Limits(max_tool_calls=5))

    class SpendsWhileReasoning(StubAgents):
        async def reason(self, incident, findings, ledger, gaps, critique):
            budget.usage += Usage(tool_calls=5)
            return await super().reason(incident, findings, ledger, gaps, critique)

    agents = SpendsWhileReasoning(critiques=[MORE, MORE])
    report = await investigate(INCIDENT, agents, budget)
    assert report.stop_reason == "tool_budget_exhausted"
    assert agents.calls["plan"] == 1
    assert agents.calls["report"] == 0


# --- LLM output is cleaned on its way through the workflow ---


async def test_made_up_citations_in_hypotheses_are_removed_and_measured():
    class FabricatingReasoner(StubAgents):
        async def reason(self, incident, findings, ledger, gaps, critique):
            result = await super().reason(incident, findings, ledger, gaps, critique)
            result.hypotheses[0].supporting.append("invented-row")
            result.hypotheses[0].contradicting.append("invented-too")
            return result

    report = await run(FabricatingReasoner())
    top = report.hypotheses[0]
    assert "invented-row" not in top.supporting and top.contradicting == []
    assert report.grounding.citations - report.grounding.valid_citations >= 4


async def test_made_up_rows_in_verifier_labels_cannot_create_support():
    class FabricatingVerifier(StubAgents):
        async def verify(self, hypotheses, ledger):
            rows = [
                RowVerdict(evidence_id=row, label="supports", reason="r")
                for row in ("invented-a", "invented-b")
            ]
            return Verification(verdicts=[HypothesisVerdict(hypothesis_id="h1", rows=rows)])

    report = await run(FabricatingVerifier())
    top = report.hypotheses[0]
    assert top.verified_supporting == [] and top.confidence == "low"
    assert report.outcome == "inconclusive"
    assert report.grounding.citations - report.grounding.valid_citations >= 2


# --- Recovery from failures ---


async def test_failed_task_is_retried_then_investigation_continues():
    agents = StubAgents(fail={"r1-dep": ConnectionError("down")})
    report = await run(agents)
    starts = [event for event in agents.events if event == ("start", "r1-dep")]
    assert len(starts) == Limits().max_task_attempts
    assert any("r1-dep failed: ConnectionError" in gap for gap in report.gaps)
    assert {c.text for c in report.facts} == {"fact r1-obs"}
    assert report.hypotheses[0].confidence == "low"


async def test_permanent_error_is_not_retried():
    agents = StubAgents(fail={"r1-dep": ValueError("bad")})
    await run(agents)
    assert len([e for e in agents.events if e == ("start", "r1-dep")]) == 1


async def test_dependent_task_still_runs_when_its_dependency_failed():
    second = TaskSpec(id="dep", agent="dependency", question="q", depends_on=["obs"])
    agents = StubAgents(plan=[OBS, second], fail={"r1-obs": ValueError("bad")})
    report = await run(agents)
    assert agents.upstream["r1-dep"] == []
    assert {c.text for c in report.facts} == {"fact r1-dep"}


@pytest.mark.parametrize("error", [ValueError("bad"), ConnectionError("down")])
async def test_task_after_mixed_success_and_failure_runs_exactly_once(error):
    join = TaskSpec(id="join", agent="dependency", question="q", depends_on=["obs", "dep"])
    agents = StubAgents(plan=[OBS, DEP, join], fail={"r1-dep": error}, delay=0.01)
    await run(agents)
    assert agents.events.count(("start", "r1-join")) == 1
    assert agents.events.count(("start", "r1-obs")) == 1
    assert agents.upstream["r1-join"] == ["finding r1-obs"]


async def test_failed_task_with_slower_sibling_is_not_dispatched_again():
    class SlowSibling(StubAgents):
        async def investigate(self, task, incident, upstream):
            self.delay = 0.05 if task.id == "r1-obs" else 0
            return await super().investigate(task, incident, upstream)

    agents = SlowSibling(fail={"r1-dep": ValueError("bad")})
    await run(agents)
    assert agents.events.count(("start", "r1-dep")) == 1
    assert agents.events.count(("start", "r1-obs")) == 1


async def test_circular_dependencies_cannot_hang_the_run():
    a = TaskSpec(id="a", agent="observability", question="q", depends_on=["b"])
    b = TaskSpec(id="b", agent="dependency", question="q", depends_on=["a"])
    agents = StubAgents(plan=[a, b])
    report = await run(agents)
    assert agents.calls["investigate"] == 0
    assert any("can never finish" in gap for gap in report.gaps)
    assert report.outcome == "inconclusive"


async def test_unknown_dependency_is_ignored():
    spec = TaskSpec(id="a", agent="observability", question="q", depends_on=["ghost"])
    agents = StubAgents(plan=[spec])
    await run(agents)
    assert agents.calls["investigate"] == 1


async def test_planner_failure_falls_back_to_a_default_plan():
    agents = StubAgents(fail={"plan": ValueError("bad")})
    report = await run(agents)
    assert agents.calls["investigate"] == 2
    assert any("planner failed" in gap for gap in report.gaps)


async def test_empty_plan_falls_back_to_a_default_plan():
    agents = StubAgents(plan=[])
    await run(agents)
    assert agents.calls["investigate"] == 2


async def test_task_count_per_round_is_capped():
    specs = [
        TaskSpec(id=f"t{i}", agent="dependency", question="q", depends_on=[]) for i in range(9)
    ]
    agents = StubAgents(plan=specs)
    await run(agents, Limits(max_tasks_per_round=3))
    assert agents.calls["investigate"] == 3


@pytest.mark.parametrize(
    ("node", "stop_reason"),
    [("reason", "no_hypotheses"), ("criticize", "critic_failed")],
)
async def test_analysis_failures_end_inconclusive_with_a_report(node, stop_reason):
    agents = StubAgents(fail={node: ValueError("bad")})
    report = await run(agents)
    assert report.outcome == "inconclusive"
    assert report.stop_reason == stop_reason


async def test_verifier_failure_means_low_confidence_not_a_crash():
    agents = StubAgents(fail={"verify": ValueError("bad")})
    report = await run(agents)
    assert report.outcome == "inconclusive"
    assert report.hypotheses[0].confidence == "low"
    assert any("verifier failed" in gap for gap in report.gaps)


async def test_report_writer_failure_uses_a_template_and_keeps_the_conclusion():
    agents = StubAgents(fail={"report": ValueError("bad")})
    report = await run(agents)
    assert report.outcome == "conclusive"
    assert report.summary.startswith("Probable root cause")
    assert any("report writer failed" in gap for gap in report.gaps)


async def test_hung_task_times_out_and_investigation_continues():
    agents = StubAgents(delay=5)
    report = await asyncio.wait_for(run(agents, Limits(node_timeout_s=0.05)), 3)
    assert report.outcome == "inconclusive"
    assert any("failed" in gap for gap in report.gaps)


# --- Extension point ---


async def test_action_policy_is_applied_to_the_report():
    calls = []

    def policy(actions):
        calls.append(actions)
        return actions

    await run(StubAgents(), action_policy=policy)
    assert calls


# --- Checkpoints ---


class Crash(BaseException):
    """Passes through error handlers, like a process dying mid-run."""


async def test_crashed_run_resumes_without_repeating_finished_work(tmp_path, caplog):
    db = str(tmp_path / "runs.sqlite")
    agents = StubAgents(fail={"verify": Crash()})
    with pytest.raises(Crash):
        await run(agents, db_path=db, thread_id="t1")
    assert agents.calls["investigate"] == 2

    healthy = StubAgents()
    with caplog.at_level(logging.WARNING):
        report = await run(healthy, db_path=db, thread_id="t1", resume=True)
    assert report.outcome == "conclusive"
    assert healthy.calls["plan"] == 0 and healthy.calls["investigate"] == 0
    assert healthy.calls["verify"] == 1
    assert "unregistered" not in caplog.text


async def test_resume_restores_usage_so_budgets_still_hold(tmp_path):
    db = str(tmp_path / "runs.sqlite")
    budget = RunBudget(Limits())
    agents = SpendingAgents(budget, Usage(tool_calls=7), fail={"verify": Crash()})
    with pytest.raises(Crash):
        await investigate(INCIDENT, agents, budget, db_path=db, thread_id="t1")

    resumed = RunBudget(Limits())
    report = await investigate(
        INCIDENT, StubAgents(), resumed, db_path=db, thread_id="t1", resume=True
    )
    assert report.usage.tool_calls == 7


# --- Budget inside agents ---


class Done(BaseModel):
    summary: str


def budgeted_agent(budget, *script, world=None):
    model = ScriptedChatModel(script=list(script))
    tools = build_tools(world or World())
    middleware = [budget.middleware(), tool_retry(retry_delay=0)]
    return model, create_agent(model, tools, response_format=Done, middleware=middleware)


PROMPT = {"messages": [{"role": "user", "content": "go"}]}


async def test_llm_usage_is_counted_through_the_callback():
    budget = RunBudget()
    _, agent = budgeted_agent(budget, reply("Done", {"summary": "x"}))
    await agent.ainvoke(PROMPT, {"callbacks": [budget]})
    assert budget.usage == Usage(input_tokens=10, output_tokens=5, llm_calls=1)


async def test_tool_budget_blocks_calls_without_marking_the_tool_failed():
    budget = RunBudget(Limits(max_tool_calls=1), usage=Usage(tool_calls=1))
    call = reply("query_logs", {"service": "api"}).tool_calls[0]

    async def never_called(request):
        raise AssertionError("tool must not run")

    request = SimpleNamespace(tool_call=call)
    message = await budget.middleware().awrap_tool_call(request, never_called)
    evidence, gaps, failed = collect_evidence([message])
    assert evidence == {} and failed == []
    assert "tool_budget_exhausted" in gaps[0]


async def test_retries_count_as_one_tool_call():
    world = World(
        logs=[{"id": "l", "ts": "t", "service": "api", "level": "INFO", "msg": "m"}],
        faults=[Fault(tool="query_logs", kind="timeout", times=2)],
    )
    budget = RunBudget()
    _, agent = budgeted_agent(
        budget,
        reply("query_logs", {"service": "api"}),
        reply("Done", {"summary": "x"}),
        world=world,
    )
    await agent.ainvoke(PROMPT, {"callbacks": [budget]})
    assert budget.usage.tool_calls == 1


async def test_exhausted_token_budget_stops_the_model_without_retrying():
    budget = RunBudget(Limits(max_tokens=1), usage=Usage(input_tokens=1))
    model, agent = budgeted_agent(budget, reply("Done", {"summary": "x"}))
    with pytest.raises(BudgetExhausted):
        await agent.ainvoke(PROMPT, {"callbacks": [budget]})
    assert len(model.calls) == 0


async def test_shared_budget_applies_across_agents():
    budget = RunBudget()
    for _ in range(2):
        _, agent = budgeted_agent(budget, reply("Done", {"summary": "x"}))
        await agent.ainvoke(PROMPT, {"callbacks": [budget]})
    assert budget.usage.llm_calls == 2
