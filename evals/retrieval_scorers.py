"""Score labeled runbook retrieval cases."""

from __future__ import annotations

from typing import Any

from incidentops.tools.query_rewrite import expand_runbook_query


def _paths_at_k(hits: list[dict[str, Any]], k: int) -> list[str]:
    return [str(hit["path"]) for hit in hits[:k]]


def recall_at_k(hits: list[dict[str, Any]], expect_paths: list[str], k: int) -> float:
    found = set(_paths_at_k(hits, k))
    expected = set(expect_paths)
    return len(found & expected) / len(expected) if expected else 0.0


def reciprocal_rank(hits: list[dict[str, Any]], expect_paths: list[str]) -> float:
    expected = set(expect_paths)
    for index, hit in enumerate(hits, start=1):
        if hit["path"] in expected:
            return 1.0 / index
    return 0.0


def top1_correct(hits: list[dict[str, Any]], expect_paths: list[str]) -> bool:
    if not hits:
        return False
    return hits[0]["path"] in expect_paths


def effective_query(case: dict[str, Any]) -> str:
    raw = str(case["query"])
    prefer = case.get("prefer")
    service = str(prefer or "")
    if case.get("apply_query_rewrite", True):
        return expand_runbook_query(raw, service=service)
    return raw


def score_case(hits: list[dict[str, Any]], case: dict[str, Any]) -> dict[str, float | bool]:
    k = int(case.get("k", 3))
    expect = list(case["expect_paths"])
    return {
        "recall_at_k": recall_at_k(hits, expect, k),
        "mrr": reciprocal_rank(hits, expect),
        "top1": top1_correct(hits, expect),
    }