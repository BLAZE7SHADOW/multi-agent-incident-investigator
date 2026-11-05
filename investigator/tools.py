"""Read-only investigation tools over simulated telemetry, with injectable faults.

Build tools per run: each set keeps its own fault counters. Tools receive only a World, so they
cannot reach a golden case's truth.
"""

import json
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from langchain.agents.middleware import PIIMatch, PIIMiddleware, ToolRetryMiddleware
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from investigator.schemas import GoldenCase, ToolResult, World

GOLDEN_PATH = Path(__file__).parent.parent / "data" / "golden.yaml"
MAX_ROWS = 20

_SECRET = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{8,}|AKIA[0-9A-Z]{16})\b|\b(?:password|token|api_key)=[^\s\"',]+"
)
_WORD = re.compile(r"\w+")
_WRAPPER = re.compile(r"^<untrusted_data>(.*)</untrusted_data>$", re.DOTALL)


class ToolFault(Exception):
    def __init__(self, tool: str, kind: str) -> None:
        super().__init__(f"{tool}: simulated {kind}")
        self.tool = tool
        self.kind = kind


def load_golden(path: Path = GOLDEN_PATH) -> list[GoldenCase]:
    data = yaml.safe_load(path.read_text())
    cases = []
    for raw in data["cases"]:
        raw["world"]["dependencies"] = data["topology"]
        cases.append(GoldenCase.model_validate(raw))
    return cases


def wrap_untrusted(result: ToolResult) -> str:
    """Mark tool output as data. Escaping `<` stops a log line closing the wrapper early."""
    body = result.model_dump_json().replace("<", "\\u003c")
    return f"<untrusted_data>{body}</untrusted_data>"


def read_tool_result(content: str) -> ToolResult:
    match = _WRAPPER.match(content)
    return ToolResult.model_validate(json.loads(match.group(1) if match else content))


def _detect_secrets(text: str) -> list[PIIMatch]:
    return [
        PIIMatch(type="secret", value=m.group(), start=m.start(), end=m.end())
        for m in _SECRET.finditer(text)
    ]


def _on_failure(exc: Exception) -> str:
    tool = getattr(exc, "tool", "unknown")
    status = getattr(exc, "kind", "error")
    return wrap_untrusted(ToolResult(tool=tool, status=status, note="failed after retries"))


def tool_middleware(retry_delay: float = 0.2) -> list:
    """Retries and redaction of incident text and tool output before either reaches a provider."""
    redact = {"apply_to_input": True, "apply_to_tool_results": True, "strategy": "redact"}
    return [
        ToolRetryMiddleware(
            max_retries=2, retry_on=(ToolFault,), on_failure=_on_failure, initial_delay=retry_delay
        ),
        PIIMiddleware("secret", detector=_detect_secrets, **redact),
        PIIMiddleware("email", **redact),
        PIIMiddleware("credit_card", **redact),
    ]


class LogQuery(BaseModel):
    service: str = Field(description="Service name, e.g. checkout")
    level: str = Field(default="", description="Optional level: INFO, WARN or ERROR")
    contains: str = Field(default="", description="Optional case-insensitive text to match")
    start: str = Field(default="", description="Optional start, e.g. 2026-03-10T14:00")
    end: str = Field(default="", description="Optional end, e.g. 2026-03-10T15:00")


class MetricQuery(BaseModel):
    service: str = Field(description="Service or resource name")
    metric: str = Field(default="", description="Optional metric name, e.g. error_rate")
    region: str = Field(default="", description="Optional region, e.g. us-east")
    start: str = Field(default="", description="Optional start, e.g. 2026-03-10T14:00")
    end: str = Field(default="", description="Optional end, e.g. 2026-03-10T15:00")


class DeploymentQuery(BaseModel):
    service: str = Field(default="", description="Optional service; empty returns all services")
    start: str = Field(default="", description="Optional start, e.g. 2026-03-10T14:00")
    end: str = Field(default="", description="Optional end, e.g. 2026-03-10T15:00")


class DependencyQuery(BaseModel):
    service: str = Field(description="Service whose upstream and downstream links to return")


class HistoryQuery(BaseModel):
    query: str = Field(description="Keywords matched against past incident reports")


def _in_window(ts: str, start: str, end: str) -> bool:
    return (not start or ts >= start) and (not end or ts <= end)


def _metric_points(row: dict[str, Any], start: str, end: str) -> list[dict[str, Any]]:
    first = datetime.fromisoformat(row["start"])
    points = []
    for index, value in enumerate(row["values"]):
        ts = (first + timedelta(minutes=index * row["step_min"])).strftime("%Y-%m-%dT%H:%M")
        if _in_window(ts, start, end):
            points.append({"ts": ts, "value": value})
    return points


def build_tools(world: World) -> list[BaseTool]:
    failures: Counter[str] = Counter()

    def respond(tool: str, rows: list[dict[str, Any]]) -> str:
        fault = next((f for f in world.faults if f.tool == tool), None)
        status, note = "ok", "" if rows else "no matching rows"
        if fault and fault.kind == "partial":
            if len(rows) > fault.max_rows:
                note = "backend returned partial results; narrow the query to see more"
                rows = rows[: fault.max_rows]
            status = "partial"
        elif fault and (fault.times is None or failures[tool] < fault.times):
            failures[tool] += 1
            raise ToolFault(tool, fault.kind)
        truncated = len(rows) > MAX_ROWS
        return wrap_untrusted(
            ToolResult(
                tool=tool, status=status, rows=rows[:MAX_ROWS], note=note, truncated=truncated
            )
        )

    def query_logs(
        service: str, level: str = "", contains: str = "", start: str = "", end: str = ""
    ) -> str:
        """Search log lines for a service, oldest first."""
        rows = [
            row
            for row in world.logs
            if row["service"] == service
            and (not level or row["level"] == level.upper())
            and contains.lower() in row["msg"].lower()
            and _in_window(row["ts"], start, end)
        ]
        return respond("query_logs", sorted(rows, key=lambda row: row["ts"]))

    def query_metrics(
        service: str, metric: str = "", region: str = "", start: str = "", end: str = ""
    ) -> str:
        """Fetch metric series for a service. Series with no points in the window are omitted."""
        rows = []
        for row in world.metrics:
            if row["service"] != service or (metric and row["metric"] != metric):
                continue
            if region and row.get("region") != region:
                continue
            points = _metric_points(row, start, end)
            if points:
                fields = {k: v for k, v in row.items() if k not in ("start", "step_min", "values")}
                rows.append({**fields, "points": points})
        return respond("query_metrics", rows)

    def get_deployments(service: str = "", start: str = "", end: str = "") -> str:
        """List deploys, config changes and rollbacks, oldest first."""
        rows = [
            row
            for row in world.deployments
            if (not service or row["service"] == service) and _in_window(row["ts"], start, end)
        ]
        return respond("get_deployments", sorted(rows, key=lambda row: row["ts"]))

    def get_dependencies(service: str) -> str:
        """List what a service depends on and what depends on it."""
        rows = [r for r in world.dependencies if service in (r["service"], r["depends_on"])]
        return respond("get_dependencies", rows)

    def search_incidents(query: str) -> str:
        """Search past incident reports by keyword, best match first."""
        words = set(_WORD.findall(query.lower()))
        scored = [(len(words & set(_WORD.findall(r["summary"].lower()))), r) for r in world.history]
        matches = sorted((s for s in scored if s[0]), key=lambda s: s[0], reverse=True)
        return respond("search_incidents", [row for _, row in matches])

    tools = (
        (query_logs, LogQuery),
        (query_metrics, MetricQuery),
        (get_deployments, DeploymentQuery),
        (get_dependencies, DependencyQuery),
        (search_incidents, HistoryQuery),
    )
    return [StructuredTool.from_function(fn, args_schema=schema) for fn, schema in tools]
