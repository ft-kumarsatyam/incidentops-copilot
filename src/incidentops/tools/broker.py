"""Unified evidence retrieval for the research node."""

from typing import Any

import httpx

from incidentops.core.config import Settings
from incidentops.core.logging import get_logger
from incidentops.rag import (
    build_runbook_retriever,
    expand_runbook_query,
    rewrite_metadata,
)
from incidentops.rag.retriever import RunbookRetriever
from incidentops.tools.github import fetch_recent_commit, is_valid_repo, normalize_github
from incidentops.tools.mcp.bridge import append_mcp_evidence
from incidentops.tools.monitoring import normalize_monitoring

log = get_logger(__name__)


def retrieve_evidence(
    state: dict[str, Any],
    settings: Settings,
    *,
    attempt: int,
    retriever: RunbookRetriever | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Return state updates and timeline lines for the research node."""
    timeline: list[str] = [f"research attempt {attempt}"]
    monitoring = normalize_monitoring(state.get("monitoring", {}))
    service = state.get("service") or str(monitoring.get("service", ""))

    github = normalize_github(state.get("github", {}))
    if state.get("github") and not github.get("repo") and "repo" in state["github"]:
        timeline.append("github repo ignored: not a valid owner/name")
    if (
        settings.github_token
        and is_valid_repo(github.get("repo"))
        and not github.get("recent_deploy")
    ):
        try:
            github = {**github, **fetch_recent_commit(github["repo"], settings.github_token)}
            timeline.append("fetched latest commit from GitHub")
        except (httpx.HTTPError, ValueError) as error:
            log.warning("research.github_failed", error=type(error).__name__)
            timeline.append(f"github unavailable ({type(error).__name__})")

    raw_query = " ".join(
        [service, state.get("summary", ""), *state.get("symptoms", [])]
    ).strip()
    search_query = expand_runbook_query(
        raw_query,
        service=service,
        symptoms=[str(s) for s in state.get("symptoms", [])],
    )
    rb = retriever or build_runbook_retriever(settings)
    runbook_hits = rb.search(
        search_query,
        settings.max_runbook_results * attempt,
        prefer=service or None,
    )
    timeline.append(f"retrieved {len(runbook_hits)} runbook chunks")
    if search_query != raw_query:
        timeline.append("expanded runbook query with service aliases")
    if monitoring.get("unverified_reason"):
        timeline.append(
            f"monitoring unusable ({monitoring['unverified_reason']}); impact is unverified"
        )
    elif monitoring.get("timed_out"):
        timeline.append("monitoring timed out; impact is unverified")

    evidence: list[dict[str, Any]] = []
    if monitoring:
        evidence.append({"source": "monitoring", "data": monitoring})
    if github:
        evidence.append({"source": "github", "data": github})
    if runbook_hits:
        evidence.append({"source": "runbooks", "data": runbook_hits})

    mcp_evidence, mcp_lines = append_mcp_evidence(state, settings)
    evidence.extend(mcp_evidence)
    timeline.extend(mcp_lines)

    update: dict[str, Any] = {
        "monitoring": monitoring,
        "github": github,
        "evidence": evidence,
        "runbook_hits": runbook_hits,
        "retries": attempt,
        "timeline": timeline,
        **rewrite_metadata(raw_query, search_query, service),
    }
    if service:
        update["service"] = service
    return update, timeline