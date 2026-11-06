"""Write the data the web viewer shows, from saved results only. No model calls.

uv run python -m evals.export_web           write frontend/public/data.json
uv run python -m evals.export_web --check   fail if the file is out of date
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

from evals.scoring import summarize
from investigator.tools import INCIDENTS_PATH
from reliability.analysis import load_scores
from reliability.coverage import CATEGORIES, TAGS, counts
from reliability.metamorphic import run_suite
from reliability.rule_eval import confusion, evaluate

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "frontend" / "public" / "data.json"
BLIND = ROOT / "data" / "blind_incidents.yaml"
EVALUATIONS = [
    {
        "id": "v1",
        "title": "v1 on the 12 assignment incidents",
        "detail": "3 runs per incident, 72 runs in total. The original design.",
        "results": ROOT / "evals" / "results" / "results.json",
    },
    {
        "id": "v2",
        "title": "v2 on the 24 blind incidents",
        "detail": "1 run per incident, 48 runs in total. Adds tie-breaking and shared findings.",
        "results": ROOT / "evals" / "results" / "blind" / "results.json",
    },
]
RUN_FIELDS = (
    "incident_id",
    "system",
    "repeat",
    "valid",
    "correct",
    "outcome",
    "top_category",
    "top_component",
    "top_confidence",
    "stop_reason",
    "wrong_conclusion",
    "missed_conclusion",
    "total_tokens",
    "latency_s",
    "tool_calls",
    "llm_calls",
)


def _incidents(path: Path, name: str) -> list[dict]:
    data = yaml.safe_load(path.read_text())
    return [{**case, "set": name} for case in data["cases"]]


def build() -> dict:
    evaluations = []
    for spec in EVALUATIONS:
        scores = load_scores(spec["results"])
        systems = sorted({s.system for s in scores}, reverse=True)
        evaluations.append(
            {
                "id": spec["id"],
                "title": spec["title"],
                "detail": spec["detail"],
                "summary": {s: summarize([r for r in scores if r.system == s]) for s in systems},
                "runs": [{f: getattr(r, f) for f in RUN_FIELDS} for r in scores],
            }
        )
    incidents = _incidents(INCIDENTS_PATH, "assignment") + _incidents(BLIND, "blind")
    rules = evaluate()
    metamorphic = {}
    for name, path in (("assignment", INCIDENTS_PATH), ("blind", BLIND)):
        results = run_suite(path)
        metamorphic[name] = [
            {"incident": r["incident"], "transform": r["transform"], "passed": r["passed"]}
            for r in results
        ]
    categories, tags = counts(incidents)
    return {
        "evaluations": evaluations,
        "incidents": incidents,
        "rule_eval": {
            "scenarios": [
                {k: r[k] for k in ("name", "set", "outcome", "top", "correct")} for r in rules
            ],
            "confusion": confusion(rules),
        },
        "metamorphic": metamorphic,
        "coverage": {
            "categories": {c: categories[c] for c in CATEGORIES},
            "tags": {t: tags[t] for t in TAGS},
        },
    }


def render(data: dict) -> str:
    return json.dumps(data, sort_keys=True, default=str, separators=(",", ":")) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if data.json is out of date")
    args = parser.parse_args()
    text = render(build())
    if args.check:
        if not OUT.exists() or OUT.read_text() != text:
            sys.exit("frontend/public/data.json is out of date: run python -m evals.export_web")
        print("data.json is up to date.")
        return
    OUT.write_text(text)
    print(f"Wrote {OUT.relative_to(ROOT)} ({len(text) // 1024} KB)")


if __name__ == "__main__":
    main()
