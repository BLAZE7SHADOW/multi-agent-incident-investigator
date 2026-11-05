"""Contracts shared by agents, tools and the graph.

Models listed in LLM_OUTPUT_SCHEMAS are sent to providers as structured-output schemas, so they
stay flat: no unions, free-form dicts or numeric bounds, which providers support unevenly.
"""

from operator import add
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
RowLabel = Literal["supports", "contradicts", "symptom", "irrelevant"]


class Limits(BaseModel):
    max_rounds: int = 2
    max_tool_calls: int = 25
    max_tokens: int = 80_000
    max_seconds: float = 300
    max_task_steps: int = 4
    max_task_tool_calls: int = 5
    max_tasks_per_round: int = 4
    node_timeout_s: float = 120


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = Field(default=0, description="Part of output_tokens")
    llm_calls: int = 0
    tool_calls: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        fields = type(self).model_fields
        return Usage(**{name: getattr(self, name) + getattr(other, name) for name in fields})

    def peak(self, other: "Usage") -> "Usage":
        fields = type(self).model_fields
        return Usage(**{n: max(getattr(self, n), getattr(other, n)) for n in fields})

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class Grounding(BaseModel):
    """Citation quality as the LLM produced it, before the evidence rules clean it up."""

    citations: int = 0
    valid_citations: int = 0
    downgraded_claims: int = 0

    def __add__(self, other: "Grounding") -> "Grounding":
        fields = type(self).model_fields
        return Grounding(**{name: getattr(self, name) + getattr(other, name) for name in fields})

    @property
    def validity(self) -> float:
        return self.valid_citations / self.citations if self.citations else 1.0


class Incident(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9-]{1,32}$")
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    service: str = Field(min_length=1, max_length=64)
    reported_at: str


class ToolResult(BaseModel):
    tool: str
    status: Literal["ok", "partial", "timeout", "error", "skipped"]
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


class HypothesisSet(BaseModel):
    hypotheses: list[Hypothesis]


class RowVerdict(BaseModel):
    evidence_id: str
    label: RowLabel = Field(
        description="supports: shows this cause. symptom: shows only the incident's effects."
    )
    reason: str


class HypothesisVerdict(BaseModel):
    hypothesis_id: str
    rows: list[RowVerdict]


class Verification(BaseModel):
    verdicts: list[HypothesisVerdict]


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


class BaselineOutput(BaseModel):
    """Everything the single-agent baseline must produce in one reply."""

    claims: list[Claim]
    gaps: list[str]
    hypotheses: list[Hypothesis]
    verification: list[HypothesisVerdict]
    verdict: Literal["accept", "insufficient"]
    summary: str
    actions: list[Action]


LLM_OUTPUT_SCHEMAS = (
    Finding,
    Plan,
    HypothesisSet,
    Verification,
    Critique,
    ReportDraft,
    BaselineOutput,
)


# --- Built by code, never by an LLM ---


class Task(TaskSpec):
    status: Literal["pending", "done", "failed"] = "pending"
    attempts: int = 0
    finding: Finding | None = None


class RankedHypothesis(Hypothesis):
    """A hypothesis with everything an LLM must not decide, computed from Verifier labels."""

    verified_supporting: list[str] = []
    verified_contradicting: list[str] = []
    verified_ratio: float = 0.0
    net_support: int = 0
    confidence: Confidence = "low"
    disqualified: str = ""
    row_labels: dict[str, RowLabel] = {}


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
    usage_by_agent: dict[str, Usage] = {}
    grounding: Grounding = Grounding()
    incident: Incident | None = None
    evidence: list[Evidence] = []
    outcome_reasons: list[str] = []
    inferences: list[Claim] = []


def _merge(left: dict, right: dict) -> dict:
    return {**left, **right}


def _peak(left: Usage, right: Usage) -> Usage:
    """Nodes report the run's running total, so parallel writes keep the highest."""
    return left.peak(right)


class InvestigationState(TypedDict, total=False):
    incident: Incident
    tasks: Annotated[dict[str, Task], _merge]
    evidence: Annotated[dict[str, Evidence], _merge]
    gaps: Annotated[list[str], add]
    failed_tools: Annotated[list[str], add]
    grounding: Annotated[Grounding, add]
    trace: Annotated[list[str], add]
    usage: Annotated[Usage, _peak]
    hypotheses: list[Hypothesis]
    verification: Verification | None
    critique: Critique | None
    round: int
    evidence_seen: int
    stop_reason: str
    report: Report


# --- Test incidents ---


class Fault(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    kind: Literal["timeout", "error", "partial"]
    times: int | None = Field(default=None, description="Failures before recovery; None = always")
    max_rows: int = Field(default=2, description="Rows returned by a partial fault")


class World(BaseModel):
    """Everything tools can see. Extra fields are rejected so no answer can be stored here."""

    model_config = ConfigDict(extra="forbid")

    logs: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    deployments: list[dict[str, Any]] = []
    dependencies: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    faults: list[Fault] = []

    def row_ids(self) -> set[str]:
        tables = (self.logs, self.metrics, self.deployments, self.dependencies, self.history)
        return {row["id"] for table in tables for row in table}


class Truth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Outcome
    category: CauseCategory
    component: str = ""
    key_evidence: list[str] = []
    trap_component: str = Field(default="", description="The wrong answer this case invites")


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident: Incident
    scenario: str
    provenance: Literal["synthetic", "production"] = "synthetic"
    reviewed_by: str = ""
    split: Literal["dev", "holdout"] = "dev"
    world: World
    truth: Truth

    @model_validator(mode="after")
    def _check_key_evidence_exists(self) -> "Case":
        missing = set(self.truth.key_evidence) - self.world.row_ids()
        if missing:
            raise ValueError(f"{self.incident.id}: unknown key_evidence {sorted(missing)}")
        return self
