"""Run the labeled incidents through a provider and write a comparison report.

python -m evals.run_evals                      # rules baseline (free, offline, deterministic)
python -m evals.run_evals --provider claude    # live model (needs ANTHROPIC_API_KEY, costs money)
python -m evals.run_evals --provider both
python -m evals.run_evals --suite guardrails   # only the adversarial / edge-case incidents
"""

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.scorers import CHECKS, case_passed, score
from incidentops.core.config import Settings
from incidentops.tools.runbooks import index_runbooks
from incidentops.workflows.incident import IncidentService

ROOT = Path(__file__).resolve().parents[1]
DATASET = Path(__file__).parent / "dataset"
SUITES = ("core", "guardrails", "all")


@dataclass
class CaseResult:
    name: str
    checks: dict[str, bool | None]
    provider: str
    fallback_reason: str | None
    input_tokens: int
    output_tokens: int

    @property
    def passed(self) -> bool:
        return case_passed(self.checks)


@dataclass
class ProviderReport:
    provider: str
    results: list[CaseResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return sum(r.passed for r in self.results) / len(self.results)

    def check_rate(self, check: str) -> str:
        values = [r.checks[check] for r in self.results if r.checks[check] is not None]
        return f"{sum(values)}/{len(values)}" if values else "n/a"


def load_cases(suite: str = "core") -> list[dict[str, Any]]:
    """``core`` is the original labeled set (also used by the architecture benchmark);
    ``guardrails`` holds the adversarial and edge-case incidents in ``dataset/guardrails/``."""
    paths: list[Path] = []
    if suite in ("core", "all"):
        paths += sorted(DATASET.glob("*.json"))
    if suite in ("guardrails", "all"):
        paths += sorted((DATASET / "guardrails").glob("*.json"))
    return [json.loads(path.read_text("utf-8")) for path in paths]


def run_provider(provider: str, cases: list[dict[str, Any]]) -> ProviderReport:
    report = ProviderReport(provider)
    for case in cases:
        with tempfile.TemporaryDirectory() as tmp:
            runbook_db = Path(tmp) / "runbooks.db"
            index_runbooks(ROOT / "runbooks", runbook_db)
            settings = Settings(
                _env_file=None,  # type: ignore[call-arg]
                incidentops_env="dev",
                ai_provider=provider,  # type: ignore[arg-type]
                database_path=Path(tmp) / "eval.db",
                runbook_db=runbook_db,
                log_level="ERROR",
                log_json=False,
            )
            service = IncidentService(settings)
            try:
                state = service.start(case["input"], actor="eval")
            finally:
                service.close()
        ai = state.get("ai", {})
        report.results.append(
            CaseResult(
                name=case["name"],
                checks=score(state, case["expect"]),
                provider=ai.get("provider", "?"),
                fallback_reason=ai.get("fallback_reason"),
                input_tokens=ai.get("input_tokens", 0),
                output_tokens=ai.get("output_tokens", 0),
            )
        )
    return report


def render(reports: list[ProviderReport]) -> str:
    lines = ["# Evaluation report", "", f"{len(reports[0].results)} labeled incidents.", ""]
    lines += ["| Provider | Cases passed | " + " | ".join(CHECKS) + " |"]
    lines += ["|---|---|" + "---|" * len(CHECKS)]
    for rep in reports:
        passed = sum(r.passed for r in rep.results)
        cells = " | ".join(rep.check_rate(c) for c in CHECKS)
        lines.append(f"| {rep.provider} | {passed}/{len(rep.results)} | {cells} |")
    for rep in reports:
        lines += [
            "",
            f"## {rep.provider}",
            "",
            "| Case | Result | Failed checks | Provider | Fallback |",
        ]
        lines += ["|---|---|---|---|---|"]
        for r in rep.results:
            failed = ", ".join(k for k, v in r.checks.items() if v is False) or "-"
            lines.append(
                f"| {r.name} | {'pass' if r.passed else 'FAIL'} | {failed} | {r.provider} "
                f"| {r.fallback_reason or '-'} |"
            )
        tokens_in = sum(r.input_tokens for r in rep.results)
        tokens_out = sum(r.output_tokens for r in rep.results)
        if tokens_in or tokens_out:
            lines += ["", f"Tokens: {tokens_in} in / {tokens_out} out (triage calls)."]
    lines += ["", "Regenerate with `make eval`."]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--provider", choices=("rules", "claude", "both"), default="rules")
    parser.add_argument("--suite", choices=SUITES, default="all")
    parser.add_argument("--min-pass", type=float, default=1.0, help="fail below this pass rate")
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "report.md")
    args = parser.parse_args()

    cases = load_cases(args.suite)
    providers = ["rules", "claude"] if args.provider == "both" else [args.provider]
    if args.provider == "both":
        settings_probe = Settings(_env_file=None)  # type: ignore[call-arg]
        if not settings_probe.anthropic_api_key:
            print(
                "ANTHROPIC_API_KEY not set; comparing rules only. "
                "Set the key and re-run for Claude.",
                file=sys.stderr,
            )
            providers = ["rules"]
    reports = [run_provider(p, cases) for p in providers]
    text = render(reports)
    args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0 if all(r.pass_rate >= args.min_pass for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
