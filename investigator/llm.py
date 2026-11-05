"""Provider selection and retry policy. Agents get their model and retries from here."""

import os
import re

from langchain.agents.middleware import ModelRetryMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

_PROVIDERS = {"gemini": "google_genai"}
_QUOTA_EXHAUSTED = re.compile(r"PerDay|insufficient_quota|credit balance", re.IGNORECASE)
_PERMANENT_STATUS = {400, 401, 403, 404, 422}
_PROGRAMMING_ERRORS = (ValueError, TypeError, LookupError, AttributeError, ImportError)


class BudgetExhausted(Exception):
    """The run's shared budget is spent. Retrying cannot help."""


def load_model() -> BaseChatModel:
    """Provider SDK retries are turned off, so `model_retry` is the only retrying layer."""
    provider = os.environ.get("AI_PROVIDER")
    model = os.environ.get("AI_MODEL")
    if not provider or not model:
        raise RuntimeError("Set AI_PROVIDER and AI_MODEL, see .env.example")
    settings: dict = {"max_retries": 0}
    if provider == "gemini":
        # The Google SDK reads 0 as "use its default"; 1 means a single attempt.
        settings = {"max_retries": 1, "thinking_level": os.environ.get("AI_THINKING", "low")}
    return init_chat_model(f"{_PROVIDERS.get(provider, provider)}:{model}", **settings)


def is_quota_exhausted(exc: Exception) -> bool:
    """A long-term quota or credit limit, unlike a per-minute rate limit that clears by itself."""
    return bool(_QUOTA_EXHAUSTED.search(str(exc)))


def is_transient(exc: Exception) -> bool:
    """Decide retries across providers, whose SDKs share no exception types."""
    if isinstance(exc, BudgetExhausted) or is_quota_exhausted(exc):
        return False
    if isinstance(exc, TimeoutError):
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return status not in _PERMANENT_STATUS and not isinstance(exc, _PROGRAMMING_ERRORS)


def model_retry(initial_delay: float = 2.0) -> ModelRetryMiddleware:
    return ModelRetryMiddleware(
        max_retries=3, retry_on=is_transient, on_failure="error", initial_delay=initial_delay
    )
