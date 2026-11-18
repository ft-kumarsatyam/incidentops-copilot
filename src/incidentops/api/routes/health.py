"""Liveness and readiness probes."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response

from incidentops.api.deps import get_service, get_settings
from incidentops.core.config import Settings
from incidentops.rag import runbook_index_status
from incidentops.workflows.incident.service import IncidentService

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness: the process is up."""
    return {"status": "ok"}


@router.get("/ready")
def ready(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[IncidentService, Depends(get_service)],
) -> dict[str, Any]:
    """Readiness: dependencies this instance needs in order to serve traffic."""
    checks: dict[str, str] = {}
    try:
        service.repo.ping()
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
    if settings.redis_url:
        # Informational: with Redis down the API still serves (stream and limiter fall back local).
        checks["redis"] = "ok" if service.events.ping() else "unavailable"
    # Informational: a deep queue or dead jobs flag a problem, but this instance is still ready.
    queue = service.jobs.stats()
    checks["queue"] = (
        f"queued={queue['queued']} running={queue['running']} dead={queue['dead']} "
        f"oldest_wait_s={queue['oldest_queued_seconds']}"
    )
    checks["runbook_index"] = runbook_index_status(settings)
    checks["n8n_actions"] = "configured" if settings.n8n_action_webhook_url else "not_configured"
    action_url = (
        settings.zapier_action_webhook_url
        if settings.action_provider == "zapier"
        else settings.n8n_action_webhook_url
    )
    checks["action_provider"] = settings.action_provider
    checks["action_webhook"] = "configured" if action_url else "not_configured"
    healthy = checks["database"] == "ok" and checks["runbook_index"] == "ok"
    checks["ai_topology"] = settings.ai_topology
    checks["mcp_enabled"] = "true" if settings.mcp_enabled else "false"
    if not healthy:
        response.status_code = 503
    return {
        "status": "ready" if healthy else "degraded",
        "checks": checks,
        "ai": {"topology": settings.ai_topology, "mcp_enabled": settings.mcp_enabled},
    }
