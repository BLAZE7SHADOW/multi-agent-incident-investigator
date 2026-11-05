import pytest
from fakes import ScriptedChatModel, reply
from langchain.agents import create_agent
from pydantic import BaseModel

from investigator.evidence import collect_evidence
from investigator.schemas import RecommendedAction, ToolResult, World
from investigator.tools import build_tools, load_cases, read_tool_result, tool_retry, wrap_untrusted
from reliability.safety import flag_risky_actions, redaction_middleware

INJECTED = next(c for c in load_cases() if c.incident.id == "INC-11")
SECRET = "sk-live-FAKE000TESTKEY"
EMAIL = "ops.oncall@example.com"


class Done(BaseModel):
    summary: str


async def run_agent(world: World, prompt: str = "investigate"):
    script = [reply("query_logs", {"service": "inventory"}), reply("Done", {"summary": "ok"})]
    model = ScriptedChatModel(script=script)
    agent = create_agent(
        model,
        build_tools(world),
        response_format=Done,
        middleware=[tool_retry(retry_delay=0), *redaction_middleware()],
    )
    result = await agent.ainvoke({"messages": [{"role": "user", "content": prompt}]})
    return model, next(m for m in result["messages"] if m.type == "tool")


def test_the_test_case_really_contains_the_secret_and_email():
    assert SECRET in str(INJECTED.world.logs) and EMAIL in str(INJECTED.world.logs)


async def test_secrets_and_emails_never_reach_the_model():
    model, message = await run_agent(INJECTED.world)
    sent = str(model.calls[-1])
    for leaked in (SECRET, EMAIL):
        assert leaked not in sent and leaked not in message.content
    assert "OOMKilled" in message.content
    assert read_tool_result(message.content).rows[2]["id"] == "log-03"


async def test_evidence_ledger_stores_only_redacted_rows():
    _, message = await run_agent(INJECTED.world)
    evidence, _, _ = collect_evidence([message])
    assert SECRET not in str(evidence) and EMAIL not in str(evidence)
    assert "log-03" in evidence


async def test_incident_text_is_redacted_before_reaching_the_model():
    model, _ = await run_agent(World(), "pasted: password=hunter2secret from a.person@example.com")
    sent = str(model.calls[0])
    assert "hunter2secret" not in sent and "a.person@example.com" not in sent


def test_injection_text_stays_inside_the_wrapper():
    rows = [r for r in INJECTED.world.logs if "SYSTEM OVERRIDE" in r["msg"]]
    wrapped = wrap_untrusted(ToolResult(tool="query_logs", status="ok", rows=rows))
    assert wrapped.endswith("</untrusted_data>") and wrapped.count("</untrusted_data>") == 1


def action(description: str, approval: bool = False) -> RecommendedAction:
    return RecommendedAction(
        description=description, risk="low", hypothesis_id="h1", requires_human_approval=approval
    )


@pytest.mark.parametrize(
    "description",
    ["Roll back payments", "Disable auth", "restart inventory pods", "Scale up redis"],
)
def test_risky_wording_needs_approval_even_when_llm_says_low_risk(description):
    assert flag_risky_actions([action(description)])[0].requires_human_approval is True


def test_safe_wording_is_left_alone():
    assert flag_risky_actions([action("Add an alert")])[0].requires_human_approval is False


def test_flag_is_never_lowered():
    assert flag_risky_actions([action("Add an alert", approval=True)])[0].requires_human_approval
