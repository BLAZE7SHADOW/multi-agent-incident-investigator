"""Contracts shared by agents, tools and the graph.

Models listed in LLM_OUTPUT_SCHEMAS are sent to providers as structured-output schemas, so they
stay flat: no unions, free-form dicts or numeric bounds, which providers support unevenly.
"""

from operator import add
from typing import Annotated, Any, Literal, TypedDict

from pydantic import AfterValidator, BaseModel, Field

AgentName = Literal["observability", "dependency"]
Source = Literal["logs", "metrics", "deployments", "dependencies", "history"]
ClaimKind = Literal["fact", "inference", "assumption"]
CauseCategory = Literal[
    "bad_deploy",
    "config_change",
    "resource_exhaustion",
    "dependency_failure",
    "expired_certificate",
    "traffic_surge",
    "unknown",
]
Confidence = Literal["low", "medium", "high"]
Outcome = Literal["conclusive", "inconclusive"]


def _clamp_unit(value: float) -> float:
    return min(1.0, max(0.0, value))


class Limits(BaseModel):
    max_rounds: int = 3
    max_tool_calls: int = 40
    max_tokens: int = 200_000
    max_seconds: float = 300
    max_task_steps: int = 6
    max_task_attempts: int = 2


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    tool_calls: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        fields = type(self).model_fields
        return Usage(**{name: getattr(self, name) + getattr(other, name) for name in fields})

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class Incident(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9-]{1,32}$")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    service: str = Field(min_length=1, max_length=64)
    reported_at: str


class ToolResult(BaseModel):
    tool: str
    status: Literal["ok", "partial", "timeout", "error"]
    rows: list[dict[str, Any]] = []
    note: str = ""
    truncated: bool = False


class Evidence(BaseModel):
    id: str
    source: Source
    content: str


# --- Agent outputs (sent to the LLM as schemas) ---


class Claim(BaseModel):
    text: str
    kind: ClaimKind
    evidence_ids: list[str] = Field(description="IDs of retrieved rows backing this claim")


class Finding(BaseModel):
    summary: str
    claims: list[Claim]
    gaps: list[str] = Field(description="What could not be determined, and why")


class TaskSpec(BaseModel):
    id: str
    agent: AgentName
    question: str
    depends_on: list[str] = Field(description="IDs of tasks whose findings this task needs")


class Plan(BaseModel):
    tasks: list[TaskSpec]


class Hypothesis(BaseModel):
    id: str
    statement: str
    category: CauseCategory
    component: str = Field(description="The service or resource at fault")
    supporting: list[str] = Field(description="Evidence IDs supporting this hypothesis")
    contradicting: list[str] = Field(description="Evidence IDs contradicting this hypothesis")
    assumptions: list[str]
    score: Annotated[float, AfterValidator(_clamp_unit)] = Field(description="Between 0 and 1")


class HypothesisSet(BaseModel):
    hypotheses: list[Hypothesis]


class EvidenceRequest(BaseModel):
    agent: AgentName
    question: str
    reason: str


class Critique(BaseModel):
    verdict: Literal["accept", "investigate", "insufficient"]
    objections: list[str]
    requests: list[EvidenceRequest]


class Action(BaseModel):
    description: str
    risk: Literal["low", "high"]
    hypothesis_id: str


class ReportDraft(BaseModel):
    summary: str
    actions: list[Action]


LLM_OUTPUT_SCHEMAS = (Finding, Plan, HypothesisSet, Critique, ReportDraft)


# --- Built by code, never by an LLM ---


class Task(TaskSpec):
    status: Literal["pending", "done", "failed"] = "pending"
    attempts: int = 0
    finding: Finding | None = None


class RankedHypothesis(Hypothesis):
    confidence: Confidence


class RecommendedAction(Action):
    requires_human_approval: bool


class Report(BaseModel):
    incident_id: str
    outcome: Outcome
    stop_reason: str
    summary: str
    hypotheses: list[RankedHypothesis]
    facts: list[Claim]
    assumptions: list[Claim]
    gaps: list[str]
    actions: list[RecommendedAction]
    usage: Usage


def _merge(left: dict, right: dict) -> dict:
    return {**left, **right}


class InvestigationState(TypedDict, total=False):
    incident: Incident
    tasks: Annotated[dict[str, Task], _merge]
    evidence: Annotated[dict[str, Evidence], _merge]
    gaps: Annotated[list[str], add]
    trace: Annotated[list[str], add]
    usage: Annotated[Usage, add]
    hypotheses: list[Hypothesis]
    critique: Critique | None
    round: int
    stop_reason: str
    report: Report
