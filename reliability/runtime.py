"""Runs an investigation with the safety extras on. Every real run should start here."""

from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel

from investigator.agents import LLMAgents
from investigator.budget import RunBudget
from investigator.graph import investigate
from investigator.llm import load_model
from investigator.schemas import Incident, Limits, Report, World
from investigator.tools import build_tools
from reliability.safety import flag_risky_actions, redaction_middleware


async def run_incident(
    incident: Incident,
    world: World,
    model: BaseChatModel | None = None,
    limits: Limits | None = None,
    db_path: str | Path = ":memory:",
    thread_id: str | None = None,
    resume: bool = False,
) -> Report:
    budget = RunBudget(limits)
    agents = LLMAgents(
        model or load_model(),
        build_tools(world),
        budget,
        extra_middleware=redaction_middleware(),
    )
    return await investigate(
        incident,
        agents,
        budget,
        db_path=str(db_path),
        thread_id=thread_id,
        action_policy=flag_risky_actions,
        resume=resume,
    )
