"""Compare the current workflow with the experimental specialist subgraph."""

import json
import statistics
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from evals.run_evals import load_cases
from evals.scorers import case_passed, score
from incidentops.agents.resilient import TriageProvider
from incidentops.agents.rules import RulesProvider
from incidentops.agents.specialists import SpecialistProvider
from incidentops.core.config import Settings
from incidentops.rag import index_runbooks
from incidentops.workflows.incident import IncidentService

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class ArchitectureResult:
    name: str
    roles: int
    case_checks: list[dict[str, bool | None]]
    latency_ms: list[float]

    @property
    def pass_rate(self) -> float:
        return sum(case_passed(checks) for checks in self.case_checks) / len(self.case_checks)

    def check_rate(self, name: str) -> float:
        values = [checks[name] for checks in self.case_checks if checks[name] is not None]
        return sum(bool(value) for value in values) / len(values) if values else 1.0

    @property
    def median_ms(self) -> float:
        return statistics.median(self.latency_ms)

    @property
    def p95_ms(self) -> float:
        ordered = sorted(self.latency_ms)
        return ordered[max(0, int(len(ordered) * 0.95 + 0.999) - 1)]


def run_architecture(
    name: str,
    roles: int,
    provider_factory: Callable[[], TriageProvider],
    cases: list[dict[str, Any]],
) -> ArchitectureResult:
    checks: list[dict[str, bool | None]] = []
    latency: list[float] = []
    for case in cases:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runbook_db = root / "runbooks.db"
            index_runbooks(ROOT / "runbooks", runbook_db)
            settings = Settings(
                _env_file=None,  # type: ignore[call-arg]
                incidentops_env="dev",
                ai_provider="rules",
                database_path=root / "eval.db",
                runbook_db=runbook_db,
                log_level="ERROR",
                log_json=False,
            )
            service = IncidentService(settings, provider=provider_factory())
            try:
                started = time.perf_counter_ns()
                state = service.start(case["input"], actor="architecture-eval")
                latency.append((time.perf_counter_ns() - started) / 1_000_000)
            finally:
                service.close()
        checks.append(score(state, case["expect"]))
    return ArchitectureResult(name, roles, checks, latency)


def recommendation(
    baseline: ArchitectureResult,
    specialists: ArchitectureResult,
) -> tuple[bool, str]:
    quality_gain = specialists.pass_rate - baseline.pass_rate
    no_safety_regression = specialists.check_rate("safety") >= baseline.check_rate("safety")
    no_grounding_regression = specialists.check_rate("grounded") >= baseline.check_rate("grounded")
    adopt = quality_gain >= 0.05 and no_safety_regression and no_grounding_regression
    if adopt:
        return True, "Specialists improve case pass rate by at least 5 points without regressions."
    if quality_gain <= 0:
        return False, "No quality gain; the additional roles and nested graph are unjustified."
    return False, "Observed gain is below the 5-point adoption threshold."


def render(baseline: ArchitectureResult, specialists: ArchitectureResult) -> str:
    adopt, reason = recommendation(baseline, specialists)
    lines = [
        "# Agent architecture evaluation",
        "",
        "Same labeled incidents, runbooks, safety checks, and deterministic logic; only the "
        "orchestration topology changes.",
        "",
        "| Architecture | Roles | Cases passed | Safety | Grounded | Median ms | p95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for result in (baseline, specialists):
        lines.append(
            f"| {result.name} | {result.roles} | {result.pass_rate:.0%} | "
            f"{result.check_rate('safety'):.0%} | {result.check_rate('grounded'):.0%} | "
            f"{result.median_ms:.2f} | {result.p95_ms:.2f} |"
        )
    lines += [
        "",
        "## Decision",
        "",
        f"**{'ADOPT' if adopt else 'REJECT'} specialist swarm for the current scope.** {reason}",
        "",
        "Adoption gate: at least 5 percentage points of case-pass improvement, no safety or "
        "grounding regression. Latency is reported, not used to excuse lower quality.",
        "",
        "CrewAI remains unnecessary: LangGraph already expresses the tested fan-out and join.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    import os

    cases = load_cases()
    baseline = run_architecture("Current graph", 2, RulesProvider, cases)
    specialists = run_architecture(
        "Parallel specialists (RoleGraph rules)", 5, SpecialistProvider, cases
    )
    report = render(baseline, specialists)
    if os.environ.get("ANTHROPIC_API_KEY"):
        report += (
            "\n## LLM specialists\n\n"
            "Set `AI_TOPOLOGY=specialists` and `AI_PROVIDER=claude` to benchmark LLM partial "
            "roles; guardrails and merge stay shared. Not run in CI (token cost).\n"
        )
    output = Path(__file__).parent / "architecture_report.md"
    output.write_text(report, encoding="utf-8")
    raw = {
        "baseline": asdict(baseline),
        "specialists": asdict(specialists),
        "adopt": recommendation(baseline, specialists)[0],
    }
    output.with_suffix(".json").write_text(json.dumps(raw, indent=2), encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
