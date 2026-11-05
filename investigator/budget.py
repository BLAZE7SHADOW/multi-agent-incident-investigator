"""One budget shared by every agent in a run. Built-in limits count per agent, so can't do this."""

import time

from langchain.agents.middleware import AgentMiddleware
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import ToolMessage

from investigator.llm import BudgetExhausted
from investigator.schemas import Limits, ToolResult, Usage
from investigator.tools import wrap_untrusted


class RunBudget(AsyncCallbackHandler):
    """Counts usage across a run. Pass it as a callback, and to agents via `middleware()`."""

    def __init__(self, limits: Limits | None = None, usage: Usage | None = None) -> None:
        self.limits = limits or Limits()
        self.usage = usage or Usage()
        self.started = time.monotonic()

    async def on_llm_end(self, response, **kwargs) -> None:
        for generations in response.generations:
            for generation in generations:
                meta = getattr(getattr(generation, "message", None), "usage_metadata", None) or {}
                self.usage += Usage(
                    input_tokens=meta.get("input_tokens", 0),
                    output_tokens=meta.get("output_tokens", 0),
                    llm_calls=1,
                )

    def stop_reason(self) -> str:
        """Why the run must stop, or an empty string if it may continue."""
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
        return await handler(request)

    async def awrap_tool_call(self, request, handler):
        call = request.tool_call
        reason = self.budget.stop_reason()
        if reason:
            result = ToolResult(tool=call["name"], status="skipped", note=reason)
            return ToolMessage(content=wrap_untrusted(result), tool_call_id=call["id"])
        self.budget.usage += Usage(tool_calls=1)
        return await handler(request)
