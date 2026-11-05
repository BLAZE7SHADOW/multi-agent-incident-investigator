"""The investigation workflow. LangGraph provides parallelism, retries, timeouts and checkpoints;
this module adds task dependencies, stop rules and the final report.
"""

import inspect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, TypedDict

import aiosqlite
from langchain_core.messages import BaseMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send
from pydantic import BaseModel

from investigator import schemas
from investigator.budget import RunBudget
from investigator.evidence import (
    clean_actions,
    clean_finding,
    clean_hypotheses,
    clean_verification,
    collect_evidence,
    decide_outcome,
    has_clear_lead,
    rank_hypotheses,
)
from investigator.schemas import (
    Action,
    Claim,
    Critique,
    Evidence,
    EvidenceRequest,
    Finding,
    Hypothesis,
    HypothesisSet,
    Incident,
    InvestigationState,
    Outcome,
    Plan,
    RankedHypothesis,
    RecommendedAction,
    Report,
    ReportDraft,
    Task,
    TaskSpec,
    Verification,
)

ActionPolicy = Callable[[Sequence[RecommendedAction]], list[RecommendedAction]]
Ledger = Mapping[str, Evidence]


@dataclass
class TaskOutcome:
    finding: Finding
    messages: list[BaseMessage]


class Agents(Protocol):
    async def plan(self, incident: Incident, critique: Critique | None, ledger: Ledger) -> Plan: ...

    async def investigate(
        self, task: Task, incident: Incident, context: list[Finding]
    ) -> TaskOutcome: ...

    async def reason(
        self,
        incident: Incident,
        findings: list[Finding],
        ledger: Ledger,
        gaps: list[str],
        critique: Critique | None,
    ) -> HypothesisSet: ...

    async def verify(self, hypotheses: list[Hypothesis], ledger: Ledger) -> Verification: ...

    async def criticize(
        self,
        incident: Incident,
        ranked: list[RankedHypothesis],
        ledger: Ledger,
        gaps: list[str],
    ) -> Critique: ...

    async def report(
        self,
        incident: Incident,
        ranked: list[RankedHypothesis],
        outcome: Outcome,
        reasons: list[str],
        gaps: list[str],
    ) -> ReportDraft: ...


class TaskInput(TypedDict):
    task: Task
    incident: Incident
    context: list[Finding]
    ledger: dict[str, Evidence]


def _default_specs(state: InvestigationState) -> list[TaskSpec]:
    """Keep investigating if the planner fails: follow the critic, or check everything once."""
    critique = state.get("critique")
    if critique and critique.requests:
        return [
            TaskSpec(id=f"req{i}", agent=r.agent, question=r.question, depends_on=[])
            for i, r in enumerate(critique.requests)
        ]
    service = state["incident"].service
    return [
        TaskSpec(
            id="symptoms",
            agent="observability",
            question=f"What do logs and metrics show for {service} and its dependencies?",
            depends_on=[],
        ),
        TaskSpec(
            id="changes",
            agent="dependency",
            question=f"What changed around {service}, what does it depend on, and has this "
            "happened before?",
            depends_on=[],
        ),
    ]


def _ready_tasks(tasks: Mapping[str, Task]) -> list[Task]:
    """Pending tasks whose dependencies have finished, whether they succeeded or failed."""
    return [
        task
        for task in tasks.values()
        if task.status == "pending"
        and all(dep in tasks and tasks[dep].status != "pending" for dep in task.depends_on)
    ]


def _context_for(task: Task, tasks: Mapping[str, Task]) -> list[Finding]:
    """A shared blackboard: the findings this task depends on first, then every other finding."""
    order = [*task.depends_on, *(t for t in tasks if t not in task.depends_on)]
    return [tasks[t].finding for t in order if t != task.id and tasks[t].finding]


def _separating_request(top: RankedHypothesis, rival: RankedHypothesis) -> Critique:
    """Two causes tie on evidence, so ask for a row that only one of them would explain."""
    question = (
        f"Two causes are equally supported: (1) {top.statement} (2) {rival.statement}. "
        "Find evidence that one predicts and the other does not."
    )
    return Critique(
        verdict="investigate",
        objections=[f"'{top.id}' and '{rival.id}' are equally supported by the evidence"],
        requests=[
            EvidenceRequest(agent=agent, question=question, reason="break a tie")
            for agent in ("observability", "dependency")
        ],
    )


def _unique(items: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(items))


def fallback_draft(
    ranked: Sequence[RankedHypothesis],
    outcome: Outcome,
    reasons: Sequence[str],
    gaps: Sequence[str],
) -> ReportDraft:
    """The report when no LLM is available or affordable. Conclusions are unaffected."""
    if outcome == "conclusive":
        top = ranked[0]
        summary = f"Probable root cause: {top.statement} ({top.confidence} confidence)."
        action = Action(
            description=f"Review the evidence for {top.component} and confirm a fix.",
            risk="low",
            hypothesis_id=top.id,
        )
        return ReportDraft(summary=summary, actions=[action])
    why = "; ".join(reasons) or "the evidence was insufficient"
    action = Action(
        description="Have an engineer gather the missing evidence: "
        + ("; ".join(_unique(gaps)) or "no specific gaps were recorded."),
        risk="low",
        hypothesis_id="",
    )
    return ReportDraft(summary=f"Inconclusive: {why}.", actions=[action])


def assemble_report(
    state: InvestigationState,
    ranked: list[RankedHypothesis],
    outcome: Outcome,
    reasons: list[str],
    draft: ReportDraft,
    budget: RunBudget,
    action_policy: ActionPolicy | None = None,
    extra_gaps: Sequence[str] = (),
) -> Report:
    """Build the final report. Everything but the summary and actions text is decided by code."""
    claims: list[Claim] = [
        claim for task in state["tasks"].values() if task.finding for claim in task.finding.claims
    ]
    assumed = [
        Claim(text=text, kind="assumption", evidence_ids=[])
        for hypothesis in ranked
        for text in hypothesis.assumptions
    ]
    actions = clean_actions(draft.actions, ranked, outcome)
    return Report(
        incident_id=state["incident"].id,
        outcome=outcome,
        outcome_reasons=reasons,
        stop_reason=state.get("stop_reason", ""),
        summary=draft.summary,
        hypotheses=ranked,
        facts=[c for c in claims if c.kind == "fact"],
        inferences=[c for c in claims if c.kind == "inference"],
        assumptions=[*(c for c in claims if c.kind == "assumption"), *assumed],
        gaps=_unique([*state.get("gaps", []), *extra_gaps]),
        actions=action_policy(actions) if action_policy else actions,
        usage=budget.usage,
        usage_by_agent=budget.by_agent,
        grounding=state.get("grounding", schemas.Grounding()),
        incident=state["incident"],
        evidence=list(state.get("evidence", {}).values()),
    )


def build_graph(
    agents: Agents,
    budget: RunBudget,
    checkpointer=None,
    action_policy: ActionPolicy | None = None,
):
    limits = budget.limits

    def _tasks_from(specs: Sequence[TaskSpec], round_no: int) -> dict[str, Task]:
        specs = specs[: limits.max_tasks_per_round]
        ids = {spec.id: f"r{round_no}-{spec.id}" for spec in specs}
        return {
            ids[spec.id]: Task(
                id=ids[spec.id],
                agent=spec.agent,
                question=spec.question,
                depends_on=[ids[dep] for dep in spec.depends_on if dep in ids],
            )
            for spec in specs
        }

    def _start_round(state: InvestigationState, specs: Sequence[TaskSpec], note: str) -> dict:
        round_no = state.get("round", 0) + 1
        tasks = _tasks_from(specs, round_no)
        return {
            "tasks": tasks,
            "round": round_no,
            "evidence_seen": len(state.get("evidence", {})),
            "trace": [f"orchestrator: round {round_no}, {note}: {sorted(tasks)}"],
            "usage": budget.usage,
        }

    async def orchestrate(state: InvestigationState) -> dict:
        plan = await agents.plan(
            state["incident"], state.get("critique"), state.get("evidence", {})
        )
        return _start_round(state, plan.tasks or _default_specs(state), "planned")

    def orchestrate_failed(state: InvestigationState, error: NodeError) -> Command:
        update = _start_round(state, _default_specs(state), "default plan")
        gaps = [f"planner failed: {type(error.error).__name__}"]
        return Command(update={**update, "gaps": gaps}, goto="schedule")

    def schedule(state: InvestigationState) -> dict:
        tasks = state["tasks"]
        pending = [task for task in tasks.values() if task.status == "pending"]
        reason = budget.stop_reason()
        update: dict = {"usage": budget.usage}
        if reason:
            update["stop_reason"] = reason
            stranded = pending
            note = f"not run: {reason}"
        else:
            stranded = pending if pending and not _ready_tasks(tasks) else []
            note = "not run: its dependencies can never finish"
        if stranded:
            update["tasks"] = {t.id: t.model_copy(update={"status": "failed"}) for t in stranded}
            update["gaps"] = [f"task {t.id} {note}" for t in stranded]
        return update

    def dispatch(state: InvestigationState) -> list[Send] | str:
        if state.get("stop_reason"):
            return "finalize"
        tasks = state["tasks"]
        ready = _ready_tasks(tasks)
        if not ready:
            return "reason"
        ledger = state.get("evidence", {})
        return [
            Send(
                "run_task",
                TaskInput(
                    task=task,
                    incident=state["incident"],
                    context=_context_for(task, tasks),
                    ledger=ledger,
                ),
            )
            for task in ready
        ]

    async def run_task(payload: TaskInput) -> dict:
        task = payload["task"]
        outcome = await agents.investigate(task, payload["incident"], payload["context"])
        evidence, gaps, failed = collect_evidence(outcome.messages)
        finding, grounding = clean_finding(outcome.finding, {**payload["ledger"], **evidence})
        done = task.model_copy(update={"status": "done", "finding": finding})
        return {
            "tasks": {task.id: done},
            "evidence": evidence,
            "gaps": [*gaps, *finding.gaps],
            "failed_tools": failed,
            "grounding": grounding,
            "trace": [f"{task.agent}: {task.id} found {sorted(evidence)}"],
            "usage": budget.usage,
        }

    def task_failed(payload: TaskInput, error: NodeError) -> Command:
        task = payload["task"]
        name = type(error.error).__name__
        update = {
            "tasks": {task.id: task.model_copy(update={"status": "failed"})},
            "gaps": [f"task {task.id} failed: {name}"],
            "trace": [f"{task.agent}: {task.id} failed: {name}"],
            "usage": budget.usage,
        }
        return Command(update=update, goto="schedule")

    async def reason(state: InvestigationState) -> dict:
        findings = [t.finding for t in state["tasks"].values() if t.finding]
        ledger = state.get("evidence", {})
        result = await agents.reason(
            state["incident"], findings, ledger, state.get("gaps", []), state.get("critique")
        )
        hypotheses, grounding = clean_hypotheses(result.hypotheses, ledger)
        return {
            "hypotheses": hypotheses,
            "grounding": grounding,
            "trace": [f"reasoning: proposed {[h.id for h in hypotheses]}"],
            "usage": budget.usage,
        }

    def reason_failed(state: InvestigationState, error: NodeError) -> Command:
        gaps = [f"reasoning failed: {type(error.error).__name__}"]
        return Command(update={"gaps": gaps, "usage": budget.usage}, goto="verify")

    async def verify(state: InvestigationState) -> dict:
        hypotheses = state.get("hypotheses", [])
        if not hypotheses:
            return {"verification": None, "usage": budget.usage}
        ledger = state.get("evidence", {})
        raw = await agents.verify(hypotheses, ledger)
        verification, grounding = clean_verification(raw, hypotheses, ledger)
        return {
            "verification": verification,
            "grounding": grounding,
            "trace": ["verifier: labelled evidence"],
            "usage": budget.usage,
        }

    def verify_failed(state: InvestigationState, error: NodeError) -> Command:
        gaps = [f"verifier failed: {type(error.error).__name__}"]
        update = {"verification": None, "gaps": gaps, "usage": budget.usage}
        return Command(update=update, goto="criticize")

    def _stop_after(state: InvestigationState, critique: Critique) -> str:
        if critique.verdict == "accept":
            return "critic_accepted"
        if critique.verdict == "insufficient":
            return "critic_insufficient"
        if not critique.requests:
            return "critic_requested_nothing"
        if budget.stop_reason():
            return budget.stop_reason()
        if state.get("round", 0) >= limits.max_rounds:
            return "max_rounds_reached"
        if len(state.get("evidence", {})) <= state.get("evidence_seen", 0):
            return "no_new_evidence"
        return ""

    def _ranked(state: InvestigationState) -> list[RankedHypothesis]:
        return rank_hypotheses(
            state.get("hypotheses", []),
            state.get("verification"),
            state.get("evidence", {}),
            state.get("failed_tools", []),
        )

    async def criticize(state: InvestigationState) -> dict:
        ranked = _ranked(state)
        if not ranked:
            stop = budget.stop_reason() or "no_hypotheses"
            return {"critique": None, "stop_reason": stop, "usage": budget.usage}
        critique = await agents.criticize(
            state["incident"], ranked, state.get("evidence", {}), state.get("gaps", [])
        )
        trace = [f"critic: {critique.verdict}, {len(critique.requests)} requests"]
        tied = critique.verdict == "accept" and ranked[0].confidence != "low"
        if tied and not has_clear_lead(ranked):
            critique = _separating_request(ranked[0], ranked[1])
            trace.append("evidence rules: top two causes tie, requesting separating evidence")
        return {
            "critique": critique,
            "stop_reason": _stop_after(state, critique),
            "trace": trace,
            "usage": budget.usage,
        }

    def critic_failed(state: InvestigationState, error: NodeError) -> Command:
        update = {
            "critique": None,
            "stop_reason": budget.stop_reason() or "critic_failed",
            "gaps": [f"critic failed: {type(error.error).__name__}"],
            "usage": budget.usage,
        }
        return Command(update=update, goto="finalize")

    def after_critic(state: InvestigationState) -> str:
        return "finalize" if state.get("stop_reason") else "orchestrate"

    async def finalize(state: InvestigationState) -> dict:
        ranked = _ranked(state)
        outcome, reasons = decide_outcome(ranked, state.get("critique"))
        gaps = state.get("gaps", [])
        if budget.stop_reason():
            draft = fallback_draft(ranked, outcome, reasons, gaps)
        else:
            draft = await agents.report(state["incident"], ranked, outcome, reasons, gaps)
        report = assemble_report(state, ranked, outcome, reasons, draft, budget, action_policy)
        return {"report": report, "usage": budget.usage, "trace": [f"reporting: {outcome}"]}

    def finalize_failed(state: InvestigationState, error: NodeError) -> Command:
        ranked = _ranked(state)
        outcome, reasons = decide_outcome(ranked, state.get("critique"))
        draft = fallback_draft(ranked, outcome, reasons, state.get("gaps", []))
        gap = f"report writer failed: {type(error.error).__name__}"
        report = assemble_report(
            state, ranked, outcome, reasons, draft, budget, action_policy, [gap]
        )
        return Command(update={"report": report, "usage": budget.usage}, goto=END)

    # No retry policy here: tools and models retry inside agents, keeping evidence already gathered.
    guarded = {"timeout": limits.node_timeout_s}

    graph = StateGraph(InvestigationState)
    graph.add_node("orchestrate", orchestrate, error_handler=orchestrate_failed, **guarded)
    graph.add_node("schedule", schedule)
    graph.add_node("run_task", run_task, error_handler=task_failed, **guarded)
    graph.add_node("reason", reason, error_handler=reason_failed, **guarded)
    graph.add_node("verify", verify, error_handler=verify_failed, **guarded)
    graph.add_node("criticize", criticize, error_handler=critic_failed, **guarded)
    graph.add_node("finalize", finalize, error_handler=finalize_failed, **guarded)

    graph.add_edge(START, "orchestrate")
    graph.add_edge("orchestrate", "schedule")
    graph.add_conditional_edges("schedule", dispatch, ["run_task", "reason", "finalize"])
    graph.add_edge("run_task", "schedule")
    graph.add_edge("reason", "verify")
    graph.add_edge("verify", "criticize")
    graph.add_conditional_edges("criticize", after_critic, ["orchestrate", "finalize"])
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)


def checkpoint_serde() -> JsonPlusSerializer:
    """Allow exactly our own contracts to be restored from a checkpoint, and nothing else."""
    models = [
        ("investigator.schemas", name)
        for name, value in inspect.getmembers(schemas, inspect.isclass)
        if issubclass(value, BaseModel) and value.__module__ == schemas.__name__
    ]
    return JsonPlusSerializer(allowed_msgpack_modules=models)


async def investigate(
    incident: Incident,
    agents: Agents,
    budget: RunBudget,
    db_path: str = ":memory:",
    thread_id: str | None = None,
    action_policy: ActionPolicy | None = None,
    resume: bool = False,
) -> Report:
    """Run an investigation to a report. With `resume`, continue `thread_id` from its checkpoint."""
    limits = budget.limits
    config = {
        "configurable": {"thread_id": thread_id or incident.id},
        "callbacks": [budget],
        "recursion_limit": limits.max_rounds * (2 * limits.max_tasks_per_round + 6) + 10,
    }
    async with aiosqlite.connect(db_path) as connection:
        saver = AsyncSqliteSaver(connection, serde=checkpoint_serde())
        app = build_graph(agents, budget, saver, action_policy)
        initial: dict | None = {"incident": incident}
        if resume:
            snapshot = await app.aget_state(config)
            budget.usage = snapshot.values.get("usage", budget.usage)
            initial = None
        result = await app.ainvoke(initial, config)
    return result["report"]
