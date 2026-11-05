import pytest
from fakes import ScriptedChatModel, reply
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

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
    model, agent = make_agent(*[StatusError(503)] * 4, reply("Plan", VALID_PLAN))
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
        (Exception("429 quota exceeded per minute, check billing"), True),
        (Exception("429 GenerateRequestsPerDayPerProjectPerModel-FreeTier"), False),
        (Exception("insufficient_quota"), False),
        (StatusError(401), False),
        (ValueError("bad schema"), False),
    ],
)
def test_is_transient(exc, expected):
    assert is_transient(exc) is expected


def test_load_model_requires_env(monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("AI_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="AI_PROVIDER"):
        load_model()


@pytest.mark.parametrize(
    ("provider", "model", "key", "client"),
    [
        ("gemini", "gemini-3.6-flash", "GEMINI_API_KEY", "ChatGoogleGenerativeAI"),
        ("anthropic", "claude-sonnet-5-5", "ANTHROPIC_API_KEY", "ChatAnthropic"),
    ],
)
def test_provider_is_chosen_by_env_without_a_network_call(
    monkeypatch, provider, model, key, client
):
    monkeypatch.setenv("AI_PROVIDER", provider)
    monkeypatch.setenv("AI_MODEL", model)
    monkeypatch.setenv(key, "test-key")
    assert type(load_model()).__name__ == client


def gemini_env(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("AI_MODEL", "gemini-3.6-flash")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")


def test_only_our_middleware_retries(monkeypatch):
    gemini_env(monkeypatch)
    assert load_model().max_retries == 1  # the Google SDK's value for a single attempt


@pytest.mark.parametrize(("env", "expected"), [(None, "LOW"), ("high", "HIGH")])
def test_gemini_thinking_defaults_to_low_and_is_configurable(monkeypatch, env, expected):
    gemini_env(monkeypatch)
    if env:
        monkeypatch.setenv("AI_THINKING", env)
    else:
        monkeypatch.delenv("AI_THINKING", raising=False)
    request = load_model()._prepare_request([HumanMessage("x")])
    assert expected in str(request["config"].thinking_config.thinking_level)
