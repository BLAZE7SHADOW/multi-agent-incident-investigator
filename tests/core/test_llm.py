import pytest
from fakes import ScriptedChatModel, reply
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model

from investigator.llm import is_transient, load_model, model_retry
from investigator.schemas import Plan

PROMPT = {"messages": [{"role": "user", "content": "plan"}]}
VALID_PLAN = {
    "tasks": [{"id": "t1", "agent": "observability", "question": "What failed?", "depends_on": []}]
}


class StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__("request failed")
        self.status_code = status_code


def make_agent(*script):
    model = ScriptedChatModel(script=list(script))
    agent = create_agent(model, [], response_format=Plan, middleware=[model_retry(initial_delay=0)])
    return model, agent


async def test_structured_output_is_validated():
    _, agent = make_agent(reply("Plan", VALID_PLAN))
    result = await agent.ainvoke(PROMPT)
    assert result["structured_response"].tasks[0].agent == "observability"


async def test_invalid_structured_output_gets_a_correction():
    model, agent = make_agent(reply("Plan", {"tasks": "not a list"}), reply("Plan", VALID_PLAN))
    result = await agent.ainvoke(PROMPT)
    assert len(result["structured_response"].tasks) == 1
    assert len(model.calls) == 2


async def test_transient_error_is_retried():
    _, agent = make_agent(StatusError(503), reply("Plan", VALID_PLAN))
    assert len((await agent.ainvoke(PROMPT))["structured_response"].tasks) == 1


async def test_retries_are_bounded_and_failure_is_raised():
    model, agent = make_agent(*[StatusError(503)] * 3, reply("Plan", VALID_PLAN))
    with pytest.raises(StatusError):
        await agent.ainvoke(PROMPT)
    assert len(model.script) == 1


async def test_permanent_error_is_not_retried():
    model, agent = make_agent(StatusError(401), reply("Plan", VALID_PLAN))
    with pytest.raises(StatusError):
        await agent.ainvoke(PROMPT)
    assert len(model.script) == 1


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (TimeoutError(), True),
        (StatusError(429), True),
        (StatusError(500), True),
        (Exception("provider connection error"), True),
        (StatusError(401), False),
        (ValueError("bad schema"), False),
    ],
)
def test_is_transient(exc, expected):
    assert is_transient(exc) is expected


def test_load_model_requires_env(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="LLM_MODEL"):
        load_model()


def test_model_spec_selects_provider_without_a_network_call(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert type(init_chat_model("anthropic:claude-sonnet-5-5")).__name__ == "ChatAnthropic"
