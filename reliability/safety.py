"""Safety layers beyond the assignment's requirements. On by default for every run."""

import re
from collections.abc import Sequence

from langchain.agents.middleware import PIIMatch, PIIMiddleware

from investigator.schemas import RecommendedAction

_SECRET = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{8,}|AKIA[0-9A-Z]{16})\b|\b(?:password|token|api_key)=[^\s\"',]+"
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_RISKY_ACTION = re.compile(
    r"\b(rollback|roll back|revert|restart|redeploy|delete|drop|disable|failover|fail over"
    r"|scale|migrate|rotate|purge|flush|kill|terminate|shut ?down)",
    re.IGNORECASE,
)


def find_sensitive(text: str) -> list[str]:
    """Secrets and email addresses left in text, for checks before data is stored."""
    return [m.group() for pattern in (_SECRET, _EMAIL) for m in pattern.finditer(text)]


def _detect_secrets(text: str) -> list[PIIMatch]:
    return [
        PIIMatch(type="secret", value=m.group(), start=m.start(), end=m.end())
        for m in _SECRET.finditer(text)
    ]


def redaction_middleware() -> list[PIIMiddleware]:
    """Redact incident text and tool output before either reaches a provider."""
    redact = {"apply_to_input": True, "apply_to_tool_results": True, "strategy": "redact"}
    return [
        PIIMiddleware("secret", detector=_detect_secrets, **redact),
        PIIMiddleware("email", **redact),
        PIIMiddleware("credit_card", **redact),
    ]


def flag_risky_actions(actions: Sequence[RecommendedAction]) -> list[RecommendedAction]:
    """Require approval for risky wording, whatever the LLM labelled it. Never lowers a flag."""
    return [
        action.model_copy(update={"requires_human_approval": True})
        if _RISKY_ACTION.search(action.description)
        else action
        for action in actions
    ]
