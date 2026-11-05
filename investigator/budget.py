"""One budget shared by every agent in a run. Built-in limits count per agent, so can't do this."""

import time
from uuid import UUID

from langchain.agents.middleware import AgentMiddleware
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import ToolMessage

from investigator.llm import BudgetExhausted, is_quota_exhausted
from investigator.schemas import Limits, ToolResult, Usage
from investigator.tools import wrap_untrusted


class RunBudget(AsyncCallbackHandler):
    """Counts usage across a run. Pass it as a callback, and to agents via `middleware()`."""

    def __init__(self, limits: Limits | None = None, usage: Usage | None = None) -> None:
        self.limits = limits or Limits()
        self.usage = usage or Usage()
        self.by_agent: dict[str, Usage] = {}
        self.quota_exhausted = False
        self.started = time.monotonic()
        self._agents: dict[UUID, str] = {}

    async def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, **kwargs):
        self._agents[run_id] = (metadata or {}).get("lc_agent_name", "unknown")

    async def on_llm_end(self, response, *, run_id=None, **kwargs) -> None:
        agent = self._agents.pop(run_id, "unknown")
        for generations in response.generations:
            for generation in generations:
                meta = getattr(getattr(generation, "message", None), "usage_metadata", None) or {}
                spent = Usage(
                    input_tokens=meta.get("input_tokens", 0),
                    output_tokens=meta.get("output_tokens", 0),
                    thinking_tokens=(meta.get("output_token_details") or {}).get("reasoning", 0),
                    llm_calls=1,
                )
                self.usage += spent
                self.by_agent[agent] = self.by_agent.get(agent, Usage()) + spent

    def stop_reason(self) -> str:
        """Why the run must stop, or an empty string if it may continue."""
        if self.quota_exhausted:
            return "provider_quota_exhausted"
        if self.usage.tool_calls >= self.limits.max_tool_calls:
            return "tool_budget_exhausted"
        if self.usage.total_tokens >= self.limits.max_tokens:
            return "token_budget_exhausted"
        if time.monotonic() - self.started >= self.limits.max_seconds:
            return "deadline_exceeded"
        return ""

    def middleware(self) -> "BudgetMiddleware":
        return BudgetMiddleware(self)


class BudgetMiddleware(AgentMiddleware):
    """Enforces the budget inside agents. Put it first, so retries don't count as new calls."""

    def __init__(self, budget: RunBudget) -> None:
        self.budget = budget

    async def awrap_model_call(self, request, handler):
        reason = self.budget.stop_reason()
        if reason:
            raise BudgetExhausted(reason)
        try:
            return await handler(request)
        except Exception as exc:
            self.budget.quota_exhausted |= is_quota_exhausted(exc)
            raise

    async def awrap_tool_call(self, request, handler):
        call = request.tool_call
        reason = self.budget.stop_reason()
        if reason:
            result = ToolResult(tool=call["name"], status="skipped", note=reason)
            return ToolMessage(content=wrap_untrusted(result), tool_call_id=call["id"])
        self.budget.usage += Usage(tool_calls=1)
        return await handler(request)
