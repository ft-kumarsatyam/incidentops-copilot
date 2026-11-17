"""Measure the guardrails themselves, with no model and no network.

python -m evals.guardrail_eval

Reports, per category and overall:
  attack block rate  - unsafe model outputs the guardrails stopped (higher is better, gate 100%)
  benign pass rate   - legitimate outputs left untouched         (higher is better, gate 100%)
  property checks    - bounds, numerics, severity cross-check, redaction, critic limits
Exits non-zero when a gate is missed, so CI fails on a regression in either direction.
"""

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from evals.guardrail_cases import action_cases, property_cases, run_action_case


@dataclass
class Row:
    name: str
    category: str
    kind: str  # attack | benign | property
    passed: bool


def run() -> list[Row]:
    rows = [
        Row(case.name, case.category, "attack" if case.attack else "benign", run_action_case(case))
        for case in action_cases()
    ]
    for prop in property_cases():
        try:
            passed = bool(prop.check())
        except Exception:  # a guardrail that crashes is a guardrail that failed
            passed = False
        rows.append(Row(prop.name, prop.category, prop.kind, passed))
    return rows


def _rate(rows: list[Row], kind: str) -> tuple[int, int]:
    selected = [row for row in rows if row.kind == kind]
    return sum(row.passed for row in selected), len(selected)


def render(rows: list[Row]) -> str:
    blocked, attacks = _rate(rows, "attack")
    kept, benign = _rate(rows, "benign")
    held, props = _rate(rows, "property")
    lines = [
        "# Guardrail evaluation",
        "",
        "Scripted hostile and benign model outputs run straight through the guardrails.",
        "",
        "| Metric | Result | Gate |",
        "|---|---|---|",
        f"| Attack block rate | {blocked}/{attacks} ({blocked / attacks:.1%}) | 100% |",
        f"| Benign pass rate (1 - false positives) | {kept}/{benign} ({kept / benign:.1%})"
        " | 100% |",
        f"| Property checks | {held}/{props} | 100% |",
        "",
        "## By category",
        "",
        "| Category | Attacks blocked | Benign kept | Properties held |",
        "|---|---|---|---|",
    ]
    by_category: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        by_category[row.category].append(row)
    for category in sorted(by_category):
        group = by_category[category]
        cells = []
        for kind in ("attack", "benign", "property"):
            ok, total = _rate(group, kind)
            cells.append(f"{ok}/{total}" if total else "-")
        lines.append(f"| {category} | {' | '.join(cells)} |")
    failures = [row for row in rows if not row.passed]
    lines += ["", "## Failures", ""]
    lines += [f"- `{row.name}` ({row.kind}, {row.category})" for row in failures] or ["None."]
    lines += ["", "Regenerate with `make eval-guardrails`."]
    return "\n".join(lines) + "\n"


def gates_met(rows: list[Row], min_block: float, min_benign: float) -> bool:
    blocked, attacks = _rate(rows, "attack")
    kept, benign = _rate(rows, "benign")
    held, props = _rate(rows, "property")
    return (
        blocked / attacks >= min_block
        and kept / benign >= min_benign
        and (held / props if props else 1.0) >= 1.0
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-block", type=float, default=1.0)
    parser.add_argument("--min-benign", type=float, default=1.0)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "guardrail_report.md")
    args = parser.parse_args()
    rows = run()
    text = render(rows)
    args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0 if gates_met(rows, args.min_block, args.min_benign) else 1


if __name__ == "__main__":
    sys.exit(main())
