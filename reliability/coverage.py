"""How well the test sets cover each root-cause category and difficulty type.

  uv run python -m reliability.coverage

Counts only, so it can be run on a blind set without revealing any single case's answer.
"""

import argparse
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SETS = {
    "assignment": ROOT / "data" / "incidents.yaml",
    "blind": ROOT / "data" / "blind_incidents.yaml",
    "golden extra": ROOT / "data" / "golden_extra.yaml",
}
CATEGORIES = (
    "bad_deploy",
    "config_change",
    "resource_exhaustion",
    "dependency_failure",
    "expired_certificate",
    "traffic_surge",
    "unknown",
)
TAGS = (
    "clean",
    "misleading_timing",
    "misleading_history",
    "conflicting_signals",
    "noisy_logs",
    "name_mismatch",
    "tool_timeout",
    "tool_partial",
    "tool_down",
    "cascading",
    "multiple_changes",
    "regional",
    "slow_recovery",
    "prompt_injection",
)
MIN_CASES = 3  # fewer than this per cell is too few to say anything


def load_sets() -> dict[str, list[dict]]:
    sets = {}
    for name, path in SETS.items():
        if path.exists():
            sets[name] = yaml.safe_load(path.read_text()).get("cases") or []
    return sets


def counts(cases: list[dict]) -> tuple[Counter, Counter]:
    categories = Counter(c["truth"]["category"] for c in cases)
    tags = Counter(tag for c in cases for tag in c.get("tags", []))
    return categories, tags


def gaps(cases: list[dict]) -> list[str]:
    categories, tags = counts(cases)
    missing = [f"category {c}: {categories[c]}" for c in CATEGORIES if categories[c] < MIN_CASES]
    missing += [f"tag {t}: {tags[t]}" for t in TAGS if tags[t] < MIN_CASES]
    return missing


def render(sets: dict[str, list[dict]]) -> str:
    names = list(sets)
    everything = [case for cases in sets.values() for case in cases]
    lines = [
        "# Test coverage\n",
        f"Cases per root-cause category and difficulty type. Fewer than {MIN_CASES} in a row is "
        "too few to measure.\n",
        "## Root-cause categories\n",
        "| Category | " + " | ".join(names) + " | Total |",
        "|---|" + "---|" * (len(names) + 1),
    ]
    per_set = {name: counts(cases) for name, cases in sets.items()}
    total = counts(everything)
    for category in CATEGORIES:
        cells = [str(per_set[n][0][category]) for n in names]
        lines.append(f"| {category} | " + " | ".join(cells) + f" | {total[0][category]} |")
    lines += ["\n## Difficulty types\n", "| Type | " + " | ".join(names) + " | Total |"]
    lines.append("|---|" + "---|" * (len(names) + 1))
    for tag in TAGS:
        cells = [str(per_set[n][1][tag]) for n in names]
        lines.append(f"| {tag} | " + " | ".join(cells) + f" | {total[1][tag]} |")
    missing = gaps(everything)
    lines.append(f"\n## Gaps across all sets ({len(everything)} cases)\n")
    lines += [f"- {gap}" for gap in missing] if missing else ["None."]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "evals" / "results" / "coverage.md")
    args = parser.parse_args()
    text = render(load_sets())
    args.out.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
