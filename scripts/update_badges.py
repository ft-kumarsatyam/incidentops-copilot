"""Write the README's numeric badges from the real eval reports and the test collection.

Each badge is a small JSON file in the shields.io "endpoint" format under assets/badges/. The
README points shields at the raw files on main, so a badge is always whatever this script last
wrote, never a hand-typed number.

    python scripts/update_badges.py          # rewrite the badge files
    python scripts/update_badges.py --check  # exit 1 if any file is stale (used in CI)

Run `make eval eval-guardrails eval-retrieval` first so the reports are current.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "badges"
GREEN, AMBER, RED = "brightgreen", "yellow", "red"


def ratio_color(passed: int, total: int) -> str:
    if total and passed == total:
        return GREEN
    return AMBER if total and passed / total >= 0.9 else RED


def parse_incident_evals(report: str) -> tuple[int, int]:
    """Cases passed and total for the first provider row, e.g. ``| rules | 37/37 | ...``."""
    match = re.search(r"^\|\s*\w+\s*\|\s*(\d+)/(\d+)\s*\|", report, re.MULTILINE)
    if not match:
        raise ValueError("no provider row with 'passed/total' in evals/report.md")
    return int(match.group(1)), int(match.group(2))


def parse_guardrails(report: str) -> tuple[int, int, int, int]:
    """(attacks blocked, attacks, benign kept, benign)."""
    attack = re.search(r"Attack block rate\s*\|\s*(\d+)/(\d+)", report)
    benign = re.search(r"Benign pass rate[^|]*\|\s*(\d+)/(\d+)", report)
    if not attack or not benign:
        raise ValueError("attack or benign row not found in evals/guardrail_report.md")
    return int(attack.group(1)), int(attack.group(2)), int(benign.group(1)), int(benign.group(2))


def parse_retrieval_rewrite(report: str) -> float:
    """Mean recall@k (percent) of the production retrieval mode, FTS plus query rewrite."""
    match = re.search(r"^\|\s*rewrite\s*\|\s*([\d.]+)%", report, re.MULTILINE)
    if not match:
        raise ValueError("no 'rewrite' row in evals/retrieval_report.md")
    return float(match.group(1))


def count_tests() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    match = re.search(r"(\d+) tests? collected", result.stdout)
    if not match:
        raise RuntimeError(f"could not count tests:\n{result.stdout[-400:]}{result.stderr[-400:]}")
    return int(match.group(1))


def badge(label: str, message: str, color: str) -> dict[str, object]:
    return {"schemaVersion": 1, "label": label, "message": message, "color": color}


def build() -> dict[str, dict[str, object]]:
    evals = ROOT / "evals"
    passed, total = parse_incident_evals((evals / "report.md").read_text(encoding="utf-8"))
    blocked, attacks, kept, benign = parse_guardrails(
        (evals / "guardrail_report.md").read_text(encoding="utf-8")
    )
    recall = parse_retrieval_rewrite((evals / "retrieval_report.md").read_text(encoding="utf-8"))
    guard_ok = (blocked, kept) == (attacks, benign)
    return {
        "incident-evals.json": badge(
            "incident evals", f"{passed}/{total}", ratio_color(passed, total)
        ),
        "guardrails.json": badge(
            "hostile outputs blocked",
            f"{blocked}/{attacks}",
            GREEN if guard_ok else ratio_color(blocked, attacks),
        ),
        "retrieval.json": badge(
            "retrieval recall",
            f"{recall:g}%",
            GREEN if recall >= 99.5 else AMBER if recall >= 90 else RED,
        ),
        "tests.json": badge("python tests", f"{count_tests()} collected", GREEN),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if badges are out of date")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    stale: list[str] = []
    for name, payload in build().items():
        text = json.dumps(payload, indent=2) + "\n"
        path = OUT / name
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(name)
        else:
            path.write_text(text, encoding="utf-8")
            print(f"{name}: {payload['message']}")
    if stale:
        print(f"stale badges: {', '.join(stale)}. Run `make badges` and commit assets/badges/.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
