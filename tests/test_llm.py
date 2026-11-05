import pytest
from fakes import ScriptedChatModel, reply
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage

from investigator.llm import LLM, LLMError, load_model
from investigator.schemas import Plan

PROMPT = [HumanMessage("plan")]
VALID_PLAN = {
    "tasks": [{"id": "t1", "agent": "observability", "question": "What failed?", "depends_on": []}]
}


class StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__("secret request body")
        self.status_code = status_code


def make_llm(*script) -> LLM:
    return LLM(ScriptedChatModel(script=list(script)), backoff_s=0)


async def test_structured_returns_validated_output_and_counts_usage():
    llm = make_llm(reply("Plan", VALID_PLAN))
    plan = await llm.structured(PROMPT, Plan)
    assert plan.tasks[0].agent == "observability"
    assert (llm.usage.input_tokens, llm.usage.output_tokens, llm.usage.llm_calls) == (10, 5, 1)


async def test_structured_gives_one_correction_and_counts_both_calls():
    llm = make_llm(reply("Plan", {"tasks": "not a list"}), reply("Plan", VALID_PLAN))
    plan = await llm.structured(PROMPT, Plan)
    assert len(plan.tasks) == 1
    assert llm.usage.llm_calls == 2
    assert "did not match" in llm.model.calls[1][-1].content


async def test_structured_fails_after_second_invalid_reply():
    bad = {"tasks": "not a list"}
    llm = make_llm(reply("Plan", bad), reply("Plan", bad))
    with pytest.raises(LLMError, match="invalid Plan"):
        await llm.structured(PROMPT, Plan)
    assert llm.usage.llm_calls == 2


async def test_transient_error_is_retried():
    llm = make_llm(StatusError(503), reply("Plan", VALID_PLAN))
    assert len((await llm.structured(PROMPT, Plan)).tasks) == 1


async def test_retries_are_bounded():
    llm = make_llm(StatusError(503), StatusError(503), StatusError(503), reply("Plan", VALID_PLAN))
    with pytest.raises(LLMError):
        await llm.structured(PROMPT, Plan)
    assert len(llm.model.script) == 1


async def test_permanent_error_is_not_retried_and_hides_content():
    llm = make_llm(StatusError(401), reply("Plan", VALID_PLAN))
    with pytest.raises(LLMError) as caught:
        await llm.structured(PROMPT, Plan)
    assert len(llm.model.script) == 1
    assert "secret" not in str(caught.value)


async def test_with_tools_counts_usage():
    llm = make_llm(reply("query_logs", {"service": "checkout"}))
    message = await llm.with_tools(PROMPT, [])
    assert message.tool_calls[0]["name"] == "query_logs"
    assert llm.usage.total_tokens == 15


def test_load_model_requires_env(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    with pytest.raises(LLMError, match="LLM_MODEL"):
        load_model()


def test_model_spec_selects_provider_without_a_network_call(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert type(init_chat_model("anthropic:claude-sonnet-5-5")).__name__ == "ChatAnthropic"
