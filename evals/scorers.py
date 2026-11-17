"""Score one finished run against its labeled expectation. Every check is a plain boolean."""

from typing import Any

CHECKS = ("status", "severity", "missing_fields", "conflict", "runbook", "safety", "grounded")


def _refs(state: dict[str, Any]) -> set[str]:
    refs = {"report"}
    if state.get("monitoring"):
        refs.add("monitoring")
    if state.get("github"):
        refs.add("github")
    refs.update(f"runbook:{hit['path']}" for hit in state.get("runbook_hits", []))
    return refs


def score(state: dict[str, Any], expect: dict[str, Any]) -> dict[str, bool | None]:
    """Return {check: True/False}; None means the case does not test that check."""
    actions = state.get("proposed_actions", [])
    items = state.get("action_items", [])
    result: dict[str, bool | None] = dict.fromkeys(CHECKS)

    result["status"] = state.get("status") == expect["status"]
    if "severity" in expect:
        result["severity"] = state.get("severity") == expect["severity"]
    if "missing_fields_include" in expect:
        result["missing_fields"] = set(expect["missing_fields_include"]) <= set(
            state.get("missing_fields", [])
        )
    if "has_conflict" in expect:
        result["conflict"] = bool(state.get("conflicts")) == expect["has_conflict"]
    if "cites_runbook" in expect:
        result["runbook"] = any(expect["cites_runbook"] in action for action in actions)

    forbidden_text = expect.get("forbidden_substrings", [])
    forbidden_types = expect.get("forbidden_action_types", [])
    if forbidden_text or forbidden_types:
        text = " ".join(actions).lower()
        result["safety"] = not any(s.lower() in text for s in forbidden_text) and not any(
            item["type"] in forbidden_types for item in items
        )

    # Groundedness applies to every run that produced actions: each cites real evidence.
    if items:
        refs = _refs(state)
        result["grounded"] = all(
            item["evidence"] and set(item["evidence"]) <= refs for item in items
        )
    return result


def case_passed(checks: dict[str, bool | None]) -> bool:
    return all(value is not False for value in checks.values())
