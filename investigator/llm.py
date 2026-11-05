"""Provider selection and retry policy. Agents get their model and retries from here."""

import os

from langchain.agents.middleware import ModelRetryMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

_PERMANENT_STATUS = {400, 401, 403, 404, 422}
_PROGRAMMING_ERRORS = (ValueError, TypeError, LookupError, AttributeError, ImportError)


def load_model() -> BaseChatModel:
    spec = os.environ.get("LLM_MODEL")
    if not spec:
        raise RuntimeError("LLM_MODEL is not set; expected <provider>:<model>, see .env.example")
    return init_chat_model(spec)


def is_transient(exc: Exception) -> bool:
    """Decide retries across providers, whose SDKs share no exception types."""
    if isinstance(exc, TimeoutError):
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return status not in _PERMANENT_STATUS and not isinstance(exc, _PROGRAMMING_ERRORS)


def model_retry(initial_delay: float = 1.0) -> ModelRetryMiddleware:
    return ModelRetryMiddleware(
        max_retries=2, retry_on=is_transient, on_failure="error", initial_delay=initial_delay
    )
