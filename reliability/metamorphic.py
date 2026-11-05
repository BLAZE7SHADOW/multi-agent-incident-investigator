"""Metamorphic tests: change an incident in a way that should not change the answer, and check.

  uv run python -m reliability.metamorphic

Each test runs the real tools, evidence collection and evidence rules, with no model calls.
Row labels come from each case's key evidence, standing in for a perfect Verifier. Expected
answers come from the original incident, so nobody chooses them by hand.

Two kinds of relation:
  same:      the answer must not change (renames, reordering, time shifts, harmless noise)
  not_more:  confidence must not rise (evidence removed, or a contradiction added)

Imports only `investigator`, so the same file can test older versions of the rules.
"""

import argparse
import copy
import random
import re
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from langchain_core.messages import ToolMessage

from investigator.evidence import (
    clean_hypotheses,
    clean_verification,
    collect_evidence,
    decide_outcome,
    rank_hypotheses,
)
from investigator.schemas import (
    Case,
    Critique,
    Hypothesis,
    HypothesisVerdict,
    RowVerdict,
    ToolResult,
    Verification,
)
from investigator.tools import ToolFault, build_tools, wrap_untrusted

ROOT = Path(__file__).resolve().parent.parent
INCIDENTS = ROOT / "data" / "incidents.yaml"
TABLES = ("logs", "metrics", "deployments", "history")
RANK = {"low": 0, "medium": 1, "high": 2}
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


def load_raw() -> tuple[list[dict], list[dict]]:
    data = yaml.safe_load(INCIDENTS.read_text())
    return data["topology"], [c for c in data["cases"] if c["truth"]["outcome"] == "conclusive"]


# --- The system under test: real tools and rules, with oracle labels ---


def _call(tool, args: dict) -> str:
    for _ in range(3):  # the same three attempts the retry middleware makes
        try:
            return tool.invoke(args)
        except ToolFault as fault:
            last = fault
    return wrap_untrusted(ToolResult(tool=last.tool, status=last.kind))


def investigate_offline(raw: dict, topology: list[dict], extra_labels: dict | None = None) -> dict:
    case = Case.model_validate({**raw, "world": {**raw["world"], "dependencies": topology}})
    tools = {tool.name: tool for tool in build_tools(case.world)}
    services = sorted(
        {row["service"] for row in topology}
        | {row["depends_on"] for row in topology}
        | {case.incident.service}
    )
    results = []
    for service in services:
        results.append(_call(tools["query_logs"], {"service": service}))
        results.append(_call(tools["query_metrics"], {"service": service}))
        results.append(_call(tools["get_dependencies"], {"service": service}))
    results.append(_call(tools["get_deployments"], {}))
    results.append(_call(tools["search_incidents"], {"query": case.incident.title}))
    messages = [ToolMessage(content=r, tool_call_id=str(i)) for i, r in enumerate(results)]
    ledger, _, failed = collect_evidence(messages)

    truth = case.truth
    labels = {row: "supports" for row in truth.key_evidence if row in ledger}
    labels |= {row: label for row, label in (extra_labels or {}).items() if row in ledger}
    hypothesis = Hypothesis(
        id="truth",
        statement="the known cause",
        category=truth.category,
        component=truth.component,
        supporting=list(truth.key_evidence),
        contradicting=[],
        assumptions=[],
    )
    rows = [RowVerdict(evidence_id=r, label=label, reason="") for r, label in labels.items()]
    raw_verification = Verification(verdicts=[HypothesisVerdict(hypothesis_id="truth", rows=rows)])
    # The same cleaning the workflow applies, so citations to unretrieved rows are dropped.
    hypotheses, _ = clean_hypotheses([hypothesis], ledger)
    verification, _ = clean_verification(raw_verification, hypotheses, ledger)
    ranked = rank_hypotheses(hypotheses, verification, ledger, failed)
    outcome, _ = decide_outcome(ranked, Critique(verdict="accept", objections=[], requests=[]))
    top = ranked[0]
    return {
        "outcome": outcome,
        "category": top.category,
        "component": top.component,
        "confidence": top.confidence,
        "net": top.net_support,
        "retrieved_key": sorted(labels),
    }


# --- Transforms. Each returns (case, topology, component renames, extra labels) ---


def _rewrite_strings(value, fn):
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, list):
        return [_rewrite_strings(v, fn) for v in value]
    if isinstance(value, dict):
        return {k: _rewrite_strings(v, fn) for k, v in value.items()}
    return value


def rename_services(raw, topology):
    names = {r["service"] for r in topology} | {r["depends_on"] for r in topology}
    renames = {name: f"{name}-api" for name in names}
    pattern = re.compile(
        r"\b(" + "|".join(sorted(map(re.escape, names), key=len, reverse=True)) + r")\b"
    )

    def fn(text):
        return pattern.sub(lambda m: renames[m.group(1)], text)

    raw = copy.deepcopy(raw)
    keep = {"id", "scenario", "provenance", "split"}
    for key in raw:
        if key not in keep:
            raw[key] = _rewrite_strings(raw[key], fn)
    raw["incident"]["id"] = raw["incident"]["id"]
    for row in raw["world"].get("logs", []) + raw["world"].get("metrics", []):
        row["id"] = row["id"]
    return raw, _rewrite_strings(topology, fn), renames, {}


def deploy_names_differ(raw, topology):
    """Different tools name the same service differently, which real systems often do."""
    raw = copy.deepcopy(raw)
    for row in raw["world"].get("deployments", []):
        row["service"] = f"{row['service']}-svc"
    return raw, topology, {}, {}


METRIC_RENAMES = {
    "error_rate": "http_5xx_ratio",
    "error_rate_5xx": "status_5xx_share",
    "latency_p99_ms": "p99_response_ms",
    "cpu": "cpu_usage",
    "memory_utilization": "mem_used_ratio",
}


def rename_metrics(raw, topology):
    raw = copy.deepcopy(raw)
    for row in raw["world"].get("metrics", []):
        row["metric"] = METRIC_RENAMES.get(row["metric"], f"{row['metric']}_v2")
    return raw, topology, {}, {}


def rename_change_kinds(raw, topology):
    kinds = {"deploy": "release", "config": "settings_change", "rollback": "revert"}
    raw = copy.deepcopy(raw)
    for row in raw["world"].get("deployments", []):
        row["kind"] = kinds.get(row["kind"], row["kind"])
    return raw, topology, {}, {}


def rename_row_ids(raw, topology):
    raw = copy.deepcopy(raw)
    rows = [row for table in TABLES for row in raw["world"].get(table, [])]
    rng = random.Random(raw["incident"]["id"])  # noqa: S311 - seeded test data, not security
    numbers = rng.sample(range(100, 999), len(rows))
    mapping = {row["id"]: f"ev-{n}" for row, n in zip(rows, numbers, strict=True)}
    for row in rows:
        row["id"] = mapping[row["id"]]
    raw["truth"]["key_evidence"] = [mapping[i] for i in raw["truth"]["key_evidence"]]
    return raw, topology, {}, {}


def shift_time(raw, topology, days=400):
    def fn(text):
        return _TIMESTAMP.sub(
            lambda m: (datetime.fromisoformat(m.group()) + timedelta(days=days)).strftime(
                "%Y-%m-%dT%H:%M"
            ),
            text,
        )

    raw = copy.deepcopy(raw)
    for key in ("incident", "world"):
        raw[key] = _rewrite_strings(raw[key], fn)
    return raw, topology, {}, {}


def shuffle_rows(raw, topology):
    raw = copy.deepcopy(raw)
    rng = random.Random(f"shuffle-{raw['incident']['id']}")  # noqa: S311 - seeded test data
    for table in TABLES:
        rng.shuffle(raw["world"].get(table, []))
    topology = copy.deepcopy(topology)
    rng.shuffle(topology)
    return raw, topology, {}, {}


def _noise(raw, count, hours=-2, level="INFO", msg="health check passed"):
    """Harmless log lines on the reported service, starting `hours` from the report time."""
    raw = copy.deepcopy(raw)
    service = raw["incident"]["service"]
    start = datetime.fromisoformat(str(raw["incident"]["reported_at"])) + timedelta(hours=hours)
    for i in range(count):
        raw["world"].setdefault("logs", []).append(
            {
                "id": f"noise-{i:02d}",
                "ts": (start + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M"),
                "service": service,
                "level": level,
                "msg": msg,
            }
        )
    return raw


def light_noise(raw, topology):
    return _noise(raw, 3), topology, {}, {}


def heavy_noise(raw, topology):
    return _noise(raw, 25), topology, {}, {}


def newer_noise(raw, topology):
    return _noise(raw, 25, hours=0), topology, {}, {}


def warning_noise(raw, topology):
    return (
        _noise(raw, 25, level="WARN", msg="deprecated config key, using default"),
        topology,
        {},
        {},
    )


def drop_key_row(raw, topology):
    raw = copy.deepcopy(raw)
    dropped = raw["truth"]["key_evidence"][0]
    for table in TABLES:
        raw["world"][table] = [r for r in raw["world"].get(table, []) if r["id"] != dropped]
    raw["truth"]["key_evidence"] = raw["truth"]["key_evidence"][1:]
    return raw, topology, {}, {}


def add_contradiction(raw, topology):
    raw = copy.deepcopy(raw)
    component = raw["truth"]["component"]
    raw["world"].setdefault("logs", []).append(
        {
            "id": "contra-01",
            "ts": str(raw["incident"]["reported_at"]),
            "service": component,
            "level": "INFO",
            "msg": "all checks passing",
        }
    )
    return raw, topology, {}, {"contra-01": "contradicts"}


TRANSFORMS = {
    "rename services": (rename_services, "same"),
    "rename metrics": (rename_metrics, "same"),
    "rename change kinds": (rename_change_kinds, "same"),
    "deploy tool uses other service names": (deploy_names_differ, "same"),
    "rename row IDs": (rename_row_ids, "same"),
    "shift time by 400 days": (shift_time, "same"),
    "shuffle row order": (shuffle_rows, "same"),
    "add 3 noise rows": (light_noise, "same"),
    "add 25 noise rows": (heavy_noise, "same"),
    "add 25 noise rows after the report": (newer_noise, "same"),
    "add 25 harmless warnings": (warning_noise, "same"),
    "drop a key evidence row": (drop_key_row, "not_more"),
    "add a contradicting row": (add_contradiction, "not_more"),
}


def check(original: dict, changed: dict, relation: str, renames: dict) -> tuple[bool, str]:
    if relation == "same":
        expected = {
            **original,
            "component": renames.get(original["component"], original["component"]),
        }
        keys = ("outcome", "category", "component", "confidence")
        diffs = [f"{k}: {expected[k]} -> {changed[k]}" for k in keys if expected[k] != changed[k]]
        return not diffs, "; ".join(diffs)
    rose = RANK[changed["confidence"]] > RANK[original["confidence"]]
    newly = original["outcome"] == "inconclusive" and changed["outcome"] == "conclusive"
    if rose or newly:
        return False, f"confidence {original['confidence']} -> {changed['confidence']}"
    return True, ""


def run_suite() -> list[dict]:
    topology, cases = load_raw()
    results = []
    for raw in cases:
        original = investigate_offline(raw, topology)
        for name, (transform, relation) in TRANSFORMS.items():
            changed_raw, changed_topology, renames, extra = transform(raw, topology)
            changed = investigate_offline(changed_raw, changed_topology, extra)
            passed, detail = check(original, changed, relation, renames)
            results.append(
                {
                    "incident": raw["incident"]["id"],
                    "transform": name,
                    "relation": relation,
                    "passed": passed,
                    "detail": detail,
                    "original": original,
                }
            )
    return results


def render(results: list[dict]) -> str:
    incidents = sorted({r["incident"] for r in results})
    lines = [
        "# Metamorphic test results\n",
        f"{len(incidents)} incidents with a known cause, each changed {len(TRANSFORMS)} ways, "
        f"for {len(results)} checks. Runs the real tools and evidence rules, with labels from "
        "each case's key evidence. No model calls.\n",
        "| Change | Relation | Passed |",
        "|---|---|---|",
    ]
    for name, (_, relation) in TRANSFORMS.items():
        group = [r for r in results if r["transform"] == name]
        lines.append(f"| {name} | {relation} | {sum(r['passed'] for r in group)}/{len(group)} |")
    passed = sum(r["passed"] for r in results)
    lines.append(f"| **Total** | | **{passed}/{len(results)}** |")
    failures = [r for r in results if not r["passed"]]
    lines.append("\n## Failures\n")
    if failures:
        lines += ["| Incident | Change | What changed |", "|---|---|---|"]
        lines += [f"| {r['incident']} | {r['transform']} | {r['detail']} |" for r in failures]
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "evals" / "results" / "metamorphic.md")
    args = parser.parse_args()
    text = render(run_suite())
    args.out.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
