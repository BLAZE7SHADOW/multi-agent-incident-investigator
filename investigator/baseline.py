"""The single-agent baseline: same model, tools, budget, evidence rules and report format.

One agent gathers evidence, reasons, checks its own work and writes the report. That is the only
difference from the multi-agent system, so the comparison measures what collaboration adds.
"""

import time
from collections.abc import Sequence

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool

from investigator.agents import _UNTRUSTED, FinishBeforeLimit, _incident
from investigator.budget import RunBudget
from investigator.evidence import (
    clean_finding,
    clean_hypotheses,
    clean_verification,
    collect_evidence,
    decide_outcome,
    rank_hypotheses,
)
from investigator.graph import ActionPolicy, assemble_report, fallback_draft
from investigator.llm import model_retry
from investigator.schemas import (
    BaselineOutput,
    Critique,
    Finding,
    Incident,
    InvestigationState,
    Report,
    ReportDraft,
    Task,
    Verification,
)
from investigator.tools import tool_retry

MAX_STEPS = 12

_BASELINE = f"""You are the Incident agent, investigating a production incident alone. You have \
tools for logs, metrics, deployments, config changes, service dependencies and past incidents.

Investigate:
- Establish a timeline, and compare against the period before the incident.
- Check upstream dependencies, not just the service showing symptoms.
- Compare regions if metrics disagree with logs or reports.
- If a result is partial or truncated, narrow your query. If a tool fails, it has already been \
retried: record a gap, and never guess what it would have shown.
- Never repeat a query. Finish as soon as the evidence lets you.

Then produce your result:
- claims: each a fact (a row states it, cite its id), an inference (your interpretation, cite the \
rows) or an assumption (no evidence, cite nothing).
- hypotheses: 2 to 4 competing root causes. List as supporting only rows that point to that \
cause, and list rows that contradict it. A cause must come before its effect. A similar past \
incident is a lead, not proof. Name the component actually at fault. Use category "unknown" if \
nothing explains the incident.
- verification: for each hypothesis, label each row that matters: supports (points to this \
cause's mechanism), contradicts (evidence against it), or symptom (a generic effect any cause \
could produce). Omit dependency rows and past incidents: they do not show what happened in this \
incident.
- verdict: accept if the evidence establishes the leading hypothesis, otherwise insufficient. An \
honest "insufficient" is a good outcome.
- summary and actions for an on-call engineer. Link actions to a hypothesis id, and mark any \
action that changes production as high risk.

{_UNTRUSTED}"""


async def investigate_baseline(
    incident: Incident,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    budget: RunBudget,
    extra_middleware: Sequence = (),
    action_policy: ActionPolicy | None = None,
) -> Report:
    """Run the baseline to a report. Failures end inconclusive, as they do in the main system."""
    budget.started = time.monotonic()
    agent = create_agent(
        model,
        list(tools),
        system_prompt=(
            f"{_BASELINE}\n\nYou have at most {MAX_STEPS} turns and "
            f"{budget.limits.max_tool_calls} tool calls."
        ),
        response_format=ToolStrategy(BaselineOutput),
        name="baseline",
        middleware=[
            budget.middleware(),
            ModelCallLimitMiddleware(run_limit=MAX_STEPS, exit_behavior="end"),
            model_retry(),
            ToolCallLimitMiddleware(run_limit=budget.limits.max_tool_calls),
            tool_retry(),
            FinishBeforeLimit(MAX_STEPS),
            *extra_middleware,
        ],
    )
    # Each turn takes several graph steps; MAX_STEPS is the real bound on work.
    config = {"callbacks": [budget], "recursion_limit": 10 * MAX_STEPS}
    state: InvestigationState = {"incident": incident, "tasks": {}}
    try:
        result = await agent.ainvoke({"messages": [HumanMessage(_incident(incident))]}, config)
    except Exception as exc:
        reason = budget.stop_reason() or "baseline_failed"
        state.update(stop_reason=reason, gaps=[f"baseline failed: {type(exc).__name__}"])
        ranked, outcome, reasons = [], "inconclusive", ["the agent failed before reaching a result"]
        draft = fallback_draft(ranked, outcome, reasons, state["gaps"])
        return assemble_report(state, ranked, outcome, reasons, draft, budget, action_policy)

    ledger, gaps, failed = collect_evidence(result["messages"])
    output: BaselineOutput | None = result.get("structured_response")
    if output is None:
        state.update(stop_reason="step_limit_reached", gaps=[*gaps, "agent reached its step limit"])
        ranked, outcome, reasons = [], "inconclusive", ["the agent never submitted a result"]
        draft = fallback_draft(ranked, outcome, reasons, state["gaps"])
        return assemble_report(state, ranked, outcome, reasons, draft, budget, action_policy)

    finding, finding_stats = clean_finding(
        Finding(summary=output.summary, claims=output.claims, gaps=output.gaps), ledger
    )
    hypotheses, hypothesis_stats = clean_hypotheses(output.hypotheses, ledger)
    verification, verification_stats = clean_verification(
        Verification(verdicts=output.verification), hypotheses, ledger
    )
    ranked = rank_hypotheses(hypotheses, verification, ledger, failed)
    # Same rule as the main system; here the agent is passing judgement on its own work.
    critique = Critique(verdict=output.verdict, objections=[], requests=[])
    outcome, reasons = decide_outcome(ranked, critique)
    task = Task(
        id="baseline",
        agent="observability",
        question=incident.title,
        depends_on=[],
        status="done",
        finding=finding,
    )
    state.update(
        tasks={task.id: task},
        gaps=[*gaps, *finding.gaps],
        stop_reason=f"agent_{output.verdict}",
        grounding=finding_stats + hypothesis_stats + verification_stats,
    )
    draft = ReportDraft(summary=output.summary, actions=output.actions)
    return assemble_report(state, ranked, outcome, reasons, draft, budget, action_policy)
