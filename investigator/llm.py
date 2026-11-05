"""Provider-agnostic LLM access. Every model call in the system goes through this module."""

import asyncio
import os
from collections.abc import Sequence
from typing import TypeVar

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel

from investigator.schemas import Usage

T = TypeVar("T", bound=BaseModel)

_PERMANENT_STATUS = {400, 401, 403, 404, 422}


class LLMError(Exception):
    """A model call failed after retries. Messages name the error type only, never its content."""


def load_model() -> BaseChatModel:
    spec = os.environ.get("LLM_MODEL")
    if not spec:
        raise LLMError("LLM_MODEL is not set; expected <provider>:<model>, see .env.example")
    return init_chat_model(spec)


def _is_permanent(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return status in _PERMANENT_STATUS


class LLM:
    """One instance per investigation, so `usage` is that run's running total."""

    def __init__(
        self,
        model: BaseChatModel,
        attempts: int = 3,
        timeout_s: float = 60,
        backoff_s: float = 1,
    ) -> None:
        self.model = model
        self.attempts = attempts
        self.timeout_s = timeout_s
        self.backoff_s = backoff_s
        self.usage = Usage()

    def _record(self, message: BaseMessage | None) -> None:
        meta = getattr(message, "usage_metadata", None) or {}
        self.usage += Usage(
            input_tokens=meta.get("input_tokens", 0),
            output_tokens=meta.get("output_tokens", 0),
            llm_calls=1,
        )

    async def _invoke(self, runnable, messages: Sequence[BaseMessage]):
        for attempt in range(self.attempts):
            try:
                return await asyncio.wait_for(runnable.ainvoke(list(messages)), self.timeout_s)
            except Exception as exc:
                if _is_permanent(exc) or attempt == self.attempts - 1:
                    raise LLMError(f"model call failed: {type(exc).__name__}") from exc
                await asyncio.sleep(self.backoff_s * 2**attempt)
        raise LLMError("model call failed: no attempts configured")

    async def with_tools(self, messages: Sequence[BaseMessage], tools: Sequence) -> AIMessage:
        message = await self._invoke(self.model.bind_tools(tools), messages)
        self._record(message)
        return message

    async def structured(self, messages: Sequence[BaseMessage], schema: type[T]) -> T:
        """Return validated output, giving the model one chance to correct an invalid reply."""
        runnable = self.model.with_structured_output(schema, include_raw=True)
        conversation = list(messages)
        for _ in range(2):
            result = await self._invoke(runnable, conversation)
            self._record(result["raw"])
            if result["parsed"] is not None:
                return result["parsed"]
            conversation.append(
                HumanMessage(
                    f"That reply did not match the required schema: {result['parsing_error']}"
                )
            )
        raise LLMError(f"model returned invalid {schema.__name__} twice")
