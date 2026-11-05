"""A scripted chat model, so tests run offline and deterministically."""

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class ScriptedChatModel(BaseChatModel):
    """Replays `script` in order. An Exception entry is raised instead of returned."""

    script: list[Any]
    calls: list[list[Any]] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls.append(list(messages))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return ChatResult(generations=[ChatGeneration(message=item)])


def reply(name: str, args: dict, input_tokens: int = 10, output_tokens: int = 5) -> AIMessage:
    """A tool-call message, which is how structured output and tool use both arrive."""
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": "call-1", "type": "tool_call"}],
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    )


class RoutingChatModel(BaseChatModel):
    """Replies per agent, chosen by its system prompt, so parallel agents stay deterministic."""

    replies: dict[str, list[Any]]
    calls: list[tuple[str, list[Any]]] = []
    bound: list[list[str]] = []

    @property
    def _llm_type(self) -> str:
        return "routing"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        self.bound.append([getattr(tool, "name", str(tool)) for tool in tools])
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        system = str(messages[0].content)
        agent = next(name for name in self.replies if f"the {name} agent" in system)
        self.calls.append((agent, list(messages)))
        item = self.replies[agent].pop(0)
        if isinstance(item, Exception):
            raise item
        return ChatResult(generations=[ChatGeneration(message=item)])
