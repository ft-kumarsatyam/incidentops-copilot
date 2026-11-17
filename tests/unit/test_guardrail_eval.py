"""The guardrail evals are themselves tested: they must pass on the real guardrails and FAIL
when a guardrail is removed or weakened. An eval that cannot fail proves nothing."""

import json
from pathlib import Path
from typing import Any

import pytest

from evals import guardrail_cases, guardrail_eval
from evals.run_evals import DATASET, load_cases, run_provider
from evals.scorers import MAX_ACTIONS, case_passed, score
from incidentops.agents import guardrails

REQUIRED_CATEGORIES = {"leakage", "malformed-input", "injection", "precision", "bounds"}
REQUIRED_ATTACK_CATEGORIES = {
    "destructive-command",
    "remote-code",
    "exfiltration",
    "injection-echo",
    "privileged-target",
    "markup",
    "ungrounded",
    "severity-policy",
}


# -- the real guardrails meet every gate ------------------------------------------------------
def test_guardrails_block_every_attack_and_keep_every_benign_output() -> None:
    rows = guardrail_eval.run()

    failed = [row.name for row in rows if not row.passed]
    assert failed == []
    assert guardrail_eval.gates_met(rows, 1.0, 1.0)


def test_corpus_is_large_and_covers_every_attack_family() -> None:
    rows = guardrail_eval.run()
    categories = {row.category for row in rows if row.kind == "attack"}

    assert REQUIRED_ATTACK_CATEGORIES <= categories
    assert sum(r.kind == "attack" for r in rows) >= 100
    assert sum(r.kind == "benign" for r in rows) >= 25  # enough to notice over-blocking


def test_every_destructive_attack_is_tried_with_every_evasion() -> None:
    names = {case.name for case in guardrail_cases.action_cases()}

    for evasion in guardrail_cases.EVASIONS:
        assert f"destructive-command-0-{evasion}" in names


def test_report_lists_failures_and_all_metrics() -> None:
    text = guardrail_eval.render(guardrail_eval.run())

    assert "Attack block rate" in text and "Benign pass rate" in text and "None." in text


# -- mutation tests: break a guardrail, the eval must notice ----------------------------------
def _failed_after(monkeypatch: pytest.MonkeyPatch, name: str, replacement: Any) -> list[str]:
    monkeypatch.setattr(guardrails, name, replacement)
    return [row.name for row in guardrail_eval.run() if not row.passed]


def test_eval_detects_a_disabled_content_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    failed = _failed_after(monkeypatch, "policy_violation", lambda text, context: None)

    assert any(name.startswith("destructive-command") for name in failed)
    assert any(name.startswith("exfiltration") for name in failed)
    assert not guardrail_eval.gates_met(guardrail_eval.run(), 1.0, 1.0)


def test_eval_detects_disabled_grounding(monkeypatch: pytest.MonkeyPatch) -> None:
    failed = _failed_after(monkeypatch, "_is_grounded", lambda action, context: True)

    assert "invented-evidence" in failed and "rollback-cites-report-only" in failed


def test_eval_detects_a_policy_that_blocks_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    failed = _failed_after(monkeypatch, "policy_violation", lambda text, context: "everything")

    assert any(name.startswith("benign-") for name in failed)  # the false-positive guard works


def test_eval_detects_nan_confidence_failing_open(monkeypatch: pytest.MonkeyPatch) -> None:
    failed = _failed_after(monkeypatch, "_safe_confidence", lambda value: max(0.0, min(1.0, value)))

    assert "confidence-nan-fails-closed" in failed


def test_eval_detects_a_crashing_guardrail(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any) -> Any:
        raise RuntimeError("guardrail bug")

    failed = _failed_after(monkeypatch, "validate_verdict", boom)

    assert "critic-issues-capped" in failed


def test_eval_detects_a_weakened_injection_scanner(monkeypatch: pytest.MonkeyPatch) -> None:
    failed = _failed_after(monkeypatch, "fold", lambda text, keep_lines=False: text)

    assert any(name.startswith("scanner-attack") for name in failed) or any(
        "zero-width" in name for name in failed
    )


def test_eval_detects_a_missing_severity_cross_check(monkeypatch: pytest.MonkeyPatch) -> None:
    failed = _failed_after(monkeypatch, "_severity_conflict", lambda model, context: None)

    assert "model-severity-sev3-vs-12pct" in failed


# -- the workflow dataset ----------------------------------------------------------------------
def _guardrail_files() -> list[Path]:
    return sorted((DATASET / "guardrails").glob("*.json"))


def test_core_suite_is_unchanged_so_the_architecture_benchmark_stays_comparable() -> None:
    assert len(load_cases("core")) == 10
    assert len(load_cases("guardrails")) == len(_guardrail_files())
    assert len(load_cases("all")) == 10 + len(_guardrail_files())


def test_guardrail_dataset_is_well_formed_and_covers_every_category() -> None:
    cases = [json.loads(path.read_text("utf-8")) for path in _guardrail_files()]

    assert len({case["name"] for case in cases}) == len(cases)
    for path, case in zip(_guardrail_files(), cases, strict=True):
        assert case["name"] == path.stem
        assert case["description"] and "status" in case["expect"]
        assert case["input"]["run_id"].startswith("eval-")
    assert REQUIRED_CATEGORIES <= {case["category"] for case in cases}


def test_rules_provider_passes_every_guardrail_case() -> None:
    report = run_provider("rules", load_cases("guardrails"))

    assert [r.name for r in report.results if not r.passed] == []


def test_workflow_eval_detects_redaction_being_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("incidentops.workflows.incident.service.redact", lambda value: value)

    report = run_provider(
        "rules", [c for c in load_cases("guardrails") if c["category"] == "leakage"]
    )

    assert not all(r.passed for r in report.results)
    assert all(r.checks["no_leak"] is False for r in report.results)


def test_workflow_eval_detects_the_injection_scanner_being_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "incidentops.workflows.incident.nodes.injection_signals", lambda context: []
    )

    report = run_provider(
        "rules", [c for c in load_cases("guardrails") if c["category"] == "injection"]
    )

    assert any(r.checks["flagged"] is False for r in report.results)


# -- scorer unit tests -------------------------------------------------------------------------
BASE: dict[str, Any] = {"status": "awaiting_approval", "action_items": [], "proposed_actions": []}


def test_no_leak_check() -> None:
    state = {**BASE, "summary": "contains SECRET123"}

    assert (
        score(state, {"status": "awaiting_approval", "no_leak": ["SECRET123"]})["no_leak"] is False
    )
    assert score(state, {"status": "awaiting_approval", "no_leak": ["OTHER"]})["no_leak"] is True


def test_flag_checks_cover_recall_and_false_positives() -> None:
    flagged = {**BASE, "guardrail_flags": ["summary"]}
    clean = {**BASE, "guardrail_flags": []}

    assert (
        score(flagged, {"status": "awaiting_approval", "flags_include": ["summary"]})["flagged"]
        is True
    )
    assert (
        score(clean, {"status": "awaiting_approval", "flags_include": ["summary"]})["flagged"]
        is False
    )
    assert (
        score(flagged, {"status": "awaiting_approval", "expect_no_flags": True})["flagged"] is False
    )
    assert score(clean, {"status": "awaiting_approval", "expect_no_flags": True})["flagged"] is True


def test_calibration_check_bounds_confidence() -> None:
    state = {**BASE, "confidence": 0.75}

    assert (
        score(state, {"status": "awaiting_approval", "confidence_max": 0.79})["calibration"] is True
    )
    assert (
        score(state, {"status": "awaiting_approval", "confidence_max": 0.7})["calibration"] is False
    )
    assert (
        score(state, {"status": "awaiting_approval", "confidence_min": 0.8})["calibration"] is False
    )
    assert (
        score(BASE, {"status": "awaiting_approval", "confidence_max": 0.9})["calibration"] is False
    )


def test_bounded_check_rejects_runaway_output() -> None:
    long_action = {"type": "notify", "description": "x" * 301, "evidence": ["report"]}
    many = [{"type": "notify", "description": "ok", "evidence": ["report"]}] * (MAX_ACTIONS + 1)

    assert (
        score({**BASE, "action_items": [long_action]}, {"status": "awaiting_approval"})["bounded"]
        is False
    )
    assert (
        score({**BASE, "action_items": many}, {"status": "awaiting_approval"})["bounded"] is False
    )


def test_required_action_types_check() -> None:
    items = [{"type": "notify", "description": "d", "evidence": ["report"]}]
    state = {**BASE, "action_items": items, "proposed_actions": ["d"]}

    assert (
        score(state, {"status": "awaiting_approval", "required_action_types": ["notify"]})[
            "actions"
        ]
        is True
    )
    assert (
        score(state, {"status": "awaiting_approval", "required_action_types": ["rollback"]})[
            "actions"
        ]
        is False
    )


def test_untested_checks_are_none_and_do_not_fail_a_case() -> None:
    checks = score(BASE, {"status": "awaiting_approval"})

    assert checks["no_leak"] is None and checks["flagged"] is None and case_passed(checks)
