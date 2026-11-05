"""Grow the golden dataset from real investigations, with a person confirming every answer.

  uv run python -m reliability.golden draft runs/<incident>.json
  (edit the draft: redact it, then fill in truth, scenario and reviewed_by)
  uv run python -m reliability.golden add data/golden_drafts/<incident>.yaml

A draft never copies the system's conclusion into `truth`, so a mistake can't become the answer.
"""

import argparse
import json
from pathlib import Path

import yaml

from investigator.schemas import Case, Report
from investigator.tools import INCIDENTS_PATH, load_cases
from reliability.safety import find_sensitive

EXTRA_PATH = INCIDENTS_PATH.with_name("golden_extra.yaml")
DRAFTS_PATH = INCIDENTS_PATH.with_name("golden_drafts")

_WORLD_TABLES = {
    "logs": "logs",
    "metrics": "metrics",
    "deployments": "deployments",
    "history": "history",
}


class GoldenError(ValueError):
    """A case is not ready to enter the golden dataset."""


def load_golden(path: Path = EXTRA_PATH) -> list[Case]:
    """The frozen assignment cases plus reviewed findings."""
    topology = yaml.safe_load(INCIDENTS_PATH.read_text())["topology"]
    extras = yaml.safe_load(path.read_text()).get("cases") or []
    return [
        *load_cases(),
        *(
            Case.model_validate({**raw, "world": {**raw["world"], "dependencies": topology}})
            for raw in extras
        ),
    ]


def draft_from_report(report: Report) -> dict:
    """A case for a person to complete. Rows were redacted before they reached the report."""
    world: dict[str, list] = {table: [] for table in _WORLD_TABLES.values()}
    for row in report.evidence:
        if row.source in _WORLD_TABLES:
            world[_WORLD_TABLES[row.source]].append(json.loads(row.content))
    return {
        "incident": report.incident.model_dump() if report.incident else {},
        "scenario": "",
        "provenance": "production",
        "split": "dev",
        "reviewed_by": "",
        "world": world,
        "truth": {"outcome": "", "category": "", "component": "", "key_evidence": []},
    }


def validate_draft(raw: dict, existing_ids: set[str]) -> Case:
    """Everything a case must pass before it can be scored against."""
    if not str(raw.get("reviewed_by", "")).strip():
        raise GoldenError("reviewed_by is empty: a person must confirm the true cause")
    if not str(raw.get("scenario", "")).strip():
        raise GoldenError("scenario is empty")
    leaked = find_sensitive(json.dumps(raw["world"]) + json.dumps(raw["incident"]))
    if leaked:
        raise GoldenError(f"{len(leaked)} possible secrets or email addresses remain: redact them")
    try:
        case = Case.model_validate(raw)
    except ValueError as exc:
        raise GoldenError(f"invalid case: {exc}") from exc
    if case.incident.id in existing_ids:
        raise GoldenError(f"{case.incident.id} already exists")
    truth = case.truth
    if truth.outcome == "conclusive" and (
        truth.category == "unknown" or not truth.component or not truth.key_evidence
    ):
        raise GoldenError("a conclusive case needs a category, component and key_evidence")
    if truth.outcome == "inconclusive" and truth.key_evidence:
        raise GoldenError("an inconclusive case cannot have key_evidence")
    return case


def add_case(draft: Path, path: Path = EXTRA_PATH) -> Case:
    raw = yaml.safe_load(draft.read_text())
    existing = {case.incident.id for case in load_golden(path)}
    case = validate_draft(raw, existing)
    stored = yaml.safe_load(path.read_text())
    stored["cases"] = [*(stored.get("cases") or []), raw]
    header = "".join(line + "\n" for line in path.read_text().splitlines() if line.startswith("#"))
    path.write_text(header + yaml.safe_dump(stored, sort_keys=False))
    return case


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("draft").add_argument("report", type=Path)
    commands.add_parser("add").add_argument("draft", type=Path)
    args = parser.parse_args()

    if args.command == "draft":
        report = Report.model_validate_json(args.report.read_text())
        DRAFTS_PATH.mkdir(exist_ok=True)
        target = DRAFTS_PATH / f"{report.incident_id}.yaml"
        top = report.hypotheses[0] if report.hypotheses else None
        hint = f"{report.outcome}: {top.category}/{top.component}" if top else report.outcome
        note = f"# The system said: {hint}. This may be wrong. Confirm the true cause yourself.\n"
        target.write_text(note + yaml.safe_dump(draft_from_report(report), sort_keys=False))
        print(f"Wrote {target}. Redact it, fill in truth, scenario and reviewed_by, then add it.")
    else:
        try:
            case = add_case(args.draft)
        except GoldenError as exc:
            parser.exit(1, f"Not added: {exc}\n")
        print(f"Added {case.incident.id} to {EXTRA_PATH.name}.")


if __name__ == "__main__":
    main()
