"""Record an investigation step by step, so the viewer can replay it from incident to report.

  uv run python -m reliability.trace <incident> [<incident> ...]   makes model calls

Writes frontend/public/traces/<incident>.json. Tool output is recorded after redaction.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.callbacks import AsyncCallbackHandler
from pydantic import BaseModel

from investigator.tools import INCIDENTS_PATH, load_cases, read_tool_result
from reliability.runtime import run_incident

ROOT = Path(__file__).resolve().parent.parent
TRACES = ROOT / "frontend" / "public" / "traces"
BLIND = ROOT / "data" / "blind_incidents.yaml"


def plain(value):
    """Pydantic models and containers to JSON-ready values."""
    if isinstance(value, BaseModel):
        return plain(value.model_dump())
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [plain(v) for v in value]
    return value if isinstance(value, str | int | float | bool | type(None)) else str(value)


class TraceRecorder(AsyncCallbackHandler):
    """Collects workflow steps (via `step`) and tool calls (as a LangChain callback), in order."""

    def __init__(self) -> None:
        self.started = time.monotonic()
        self.events: list[dict] = []
        self._open: dict = {}

    def _now(self) -> float:
        return round(time.monotonic() - self.started, 2)

    def step(self, node: str, update: dict) -> None:
        self.events.append(
            {"t": self._now(), "type": "step", "node": node, "update": plain(update)}
        )

    async def on_tool_start(
        self, serialized, input_str, *, run_id, metadata=None, inputs=None, **kw
    ):
        name = (serialized or {}).get("name") or kw.get("name", "tool")
        agent = (metadata or {}).get("lc_agent_name", "")
        self._open[run_id] = (name, agent, inputs or input_str, self._now())

    async def on_tool_end(self, output, *, run_id, **kwargs):
        if run_id not in self._open:
            return
        name, agent, args, started = self._open.pop(run_id)
        content = getattr(output, "content", output)
        try:
            result = read_tool_result(str(content))
            status, rows, note = result.status, [r.get("id") for r in result.rows], result.note
        except ValueError:
            status, rows, note = "rejected", [], str(content)[:200]
        self.events.append(
            {
                "t": started,
                "type": "tool",
                "agent": agent,
                "tool": name,
                "args": plain(args),
                "status": status,
                "rows": rows,
                "note": note,
                "seconds": round(self._now() - started, 2),
            }
        )

    async def on_tool_error(self, error, *, run_id, **kwargs):
        if run_id in self._open:
            name, agent, args, started = self._open.pop(run_id)
            self.events.append(
                {
                    "t": started,
                    "type": "tool",
                    "agent": agent,
                    "tool": name,
                    "args": plain(args),
                    "status": f"failed: {type(error).__name__}, retrying",
                    "rows": [],
                    "note": "",
                    "seconds": round(self._now() - started, 2),
                }
            )


async def record(case) -> dict:
    recorder = TraceRecorder()
    report = await run_incident(
        case.incident, case.world, on_step=recorder.step, callbacks=[recorder]
    )
    events = sorted(recorder.events, key=lambda e: e["t"])
    return {
        "incident": plain(case.incident),
        "scenario": case.scenario,
        "tags": case.tags,
        "truth": plain(case.truth),
        "world": plain(case.world.model_dump(exclude={"dependencies"})),
        "events": events,
        "report": plain(report),
        "seconds": round(recorder._now(), 1),
    }


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("incidents", nargs="+")
    args = parser.parse_args()
    cases = {c.incident.id: c for c in [*load_cases(INCIDENTS_PATH), *load_cases(BLIND)]}
    TRACES.mkdir(parents=True, exist_ok=True)
    for incident in args.incidents:
        trace = await record(cases[incident])
        (TRACES / f"{incident}.json").write_text(json.dumps(trace, indent=1, default=str))
        usage = trace["report"]["usage"]
        tokens = usage["input_tokens"] + usage["output_tokens"]
        print(
            f"{incident}: {trace['report']['outcome']}, {tokens} tokens, "
            f"{trace['seconds']}s, {len(trace['events'])} events"
        )
    index = sorted(p.stem for p in TRACES.glob("*.json") if p.stem != "index")
    (TRACES / "index.json").write_text(json.dumps(index))


if __name__ == "__main__":
    asyncio.run(main())
