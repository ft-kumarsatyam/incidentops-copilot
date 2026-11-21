"""Incident endpoints."""

import hashlib
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from incidentops.api.deps import (
    get_service,
    get_settings,
    require,
    verify_inbound_signature,
)
from incidentops.api.events import event_stream
from incidentops.core.config import Settings
from incidentops.core.redact import redact
from incidentops.security import Principal
from incidentops.workflows.incident.service import IN_PROGRESS, IncidentService

RUN_ID = r"^[A-Za-z0-9._:-]{1,128}$"
Symptom = Annotated[str, StringConstraints(max_length=500)]

router = APIRouter(prefix="/incidents", tags=["incidents"])
Service = Annotated[IncidentService, Depends(get_service)]
# Each route names the permission it needs; the dependency authenticates and enforces it.
Reader = Annotated[Principal, Depends(require("incident:read"))]
Creator = Annotated[Principal, Depends(require("incident:create"))]
Retrier = Annotated[Principal, Depends(require("incident:retry"))]
Approver = Annotated[Principal, Depends(require("incident:decide"))]
RunIdPath = Annotated[str, Path(pattern=RUN_ID)]
IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^[\x21-\x7e]+$"),
]
# What must match for a repeated Idempotency-Key to count as the same request.
_IDEMPOTENT_FIELDS = ("summary", "service", "reporter", "channel_id", "thread_ts", "symptoms")


class IncidentRequest(BaseModel):
    run_id: str | None = Field(default=None, pattern=RUN_ID)
    channel_id: str = Field(min_length=1, max_length=64)
    thread_ts: str = Field(min_length=1, max_length=64)
    reporter: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=2000)
    service: str = Field(default="", max_length=128)
    owner: str = Field(default="", max_length=128)
    symptoms: list[Symptom] = Field(default_factory=list, max_length=20)
    monitoring: dict[str, Any] = Field(default_factory=dict, max_length=50)
    github: dict[str, Any] = Field(default_factory=dict, max_length=50)


class DecisionRequest(BaseModel):
    idempotency_key: str = Field(min_length=1)
    decision: Literal["approved", "rejected"]
    approved_actions: list[str] | None = Field(
        default=None,
        description="Subset of proposed_actions to approve. Omit to approve all of them.",
    )


class ActionItemOut(BaseModel):
    type: str
    description: str
    evidence: list[str] = Field(default_factory=list)


class IncidentOut(BaseModel):
    """The incident as the API returns it. Unknown fields pass through untouched."""

    model_config = ConfigDict(extra="allow")

    run_id: str
    idempotency_key: str = ""
    status: str
    severity: str | None = None
    confidence: float | None = None
    summary: str = ""
    service: str = ""
    owner: str = ""
    reporter: str = ""
    symptoms: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    proposed_actions: list[str] = Field(default_factory=list)
    approved_actions: list[str] = Field(default_factory=list)
    action_items: list[ActionItemOut] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    runbook_hits: list[dict[str, Any]] = Field(default_factory=list)
    timeline: list[str] = Field(default_factory=list)
    retries: int = 0
    approval: str | None = None
    action_dispatch: str | None = None
    ai: dict[str, Any] | None = None
    ai_review: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None


class AuditEntry(BaseModel):
    event: str
    actor: str
    detail: dict[str, Any]
    at: str


@router.get("", response_model=list[IncidentOut])
def list_incidents(
    service: Service,
    principal: Reader,
    response: Response,
    status: Annotated[list[str] | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    items, total = service.search(
        statuses=status, query=q, limit=limit, offset=offset, tenant=principal.tenant
    )
    response.headers["X-Total-Count"] = str(total)
    return items


def _idempotent_run_id(tenant: str, key: str) -> str:
    """A run id derived from the caller's key, salted with the tenant so two tenants that happen
    to pick the same key never collide."""
    return "idem-" + hashlib.sha256(f"{tenant}\n{key}".encode()).hexdigest()[:32]


def _same_request(stored: dict[str, Any], request: dict[str, Any]) -> bool:
    clean = redact(request)  # the stored run was redacted before it was saved
    return all(
        stored.get(name) == clean.get(name, "" if name != "symptoms" else [])
        for name in _IDEMPOTENT_FIELDS
    )


def _wait_seconds(prefer: str | None, ceiling: float) -> float:
    """Seconds asked for by ``Prefer: wait=N`` (RFC 7240), capped. 0 when absent or malformed."""
    for token in (prefer or "").split(","):
        name, _, value = token.strip().partition("=")
        if name.strip().lower() == "wait":
            try:
                return max(0.0, min(float(value.strip().strip('"')), ceiling))
            except ValueError:
                return 0.0
    return 0.0


@router.post(
    "",
    response_model=IncidentOut,
    dependencies=[Depends(verify_inbound_signature)],
    responses={
        202: {
            "model": IncidentOut,
            "description": "Queued for analysis (EXECUTION_MODE=async). Poll GET /incidents/{id}"
            " or watch /incidents/stream. Send `Prefer: wait=N` to wait for the result.",
        }
    },
)
def create_incident(
    request: IncidentRequest,
    service: Service,
    principal: Creator,
    settings: Annotated[Settings, Depends(get_settings)],
    response: Response,
    prefer: Annotated[str | None, Header()] = None,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    payload = request.model_dump(exclude_none=True)
    if idempotency_key:
        if request.run_id:
            raise HTTPException(
                status_code=422, detail="send either run_id or an Idempotency-Key, not both"
            )
        run_id = _idempotent_run_id(principal.tenant, idempotency_key)
        stored = service.repo.get(run_id, tenant=principal.tenant)
        if stored is not None:
            if not _same_request(stored, payload):
                raise HTTPException(
                    status_code=409,
                    detail="this Idempotency-Key was already used with a different request",
                )
            response.headers["Idempotent-Replayed"] = "true"
            return service.get(run_id, principal.tenant)
        payload["run_id"] = run_id
    state = service.create(payload, actor=principal.actor, tenant=principal.tenant)
    if state.get("status") in IN_PROGRESS:
        wait = _wait_seconds(prefer, settings.max_wait_seconds)
        if wait:
            state = service.wait_for(state["run_id"], wait, tenant=principal.tenant)
        if state.get("status") in IN_PROGRESS:
            response.status_code = 202
            response.headers["Location"] = f"/incidents/{state['run_id']}"
            response.headers["Retry-After"] = "2"
    return state


@router.get("/stream", response_class=StreamingResponse)
async def stream(request: Request, service: Service, principal: Reader) -> StreamingResponse:
    """Server-Sent Events: one `incident` event whenever one of your runs changes."""
    return StreamingResponse(
        event_stream(
            service.events,
            request.is_disconnected,
            accept=lambda event: event.get("tenant") == principal.tenant,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{run_id}", response_model=IncidentOut)
def get_incident(
    run_id: RunIdPath,
    service: Service,
    principal: Reader,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    state = service.get(run_id, principal.tenant)
    if settings.audit_reads:
        service.repo.add_audit(run_id, "read", principal.actor)
    return state


@router.get("/{run_id}/audit", response_model=list[AuditEntry])
def get_audit(run_id: RunIdPath, service: Service, principal: Reader) -> list[dict[str, Any]]:
    service.get(run_id, principal.tenant)
    return service.audit(run_id, principal.tenant)


@router.post("/{run_id}/decision", response_model=IncidentOut)
def decide(
    run_id: RunIdPath, request: DecisionRequest, service: Service, principal: Approver
) -> dict[str, Any]:
    return service.decide(
        run_id,
        request.idempotency_key,
        request.decision,
        approved_actions=request.approved_actions,
        principal=principal,
    )


@router.post("/{run_id}/retry-dispatch", response_model=IncidentOut)
def retry_dispatch(run_id: RunIdPath, service: Service, principal: Retrier) -> dict[str, Any]:
    return service.retry_dispatch(run_id, actor=principal.actor, tenant=principal.tenant)


@router.post("/{run_id}/retry-run", response_model=IncidentOut)
def retry_run(run_id: RunIdPath, service: Service, principal: Retrier) -> dict[str, Any]:
    """Re-queue a run whose analysis failed (status ``errored``) with a fresh set of attempts."""
    return service.retry_run(run_id, actor=principal.actor, tenant=principal.tenant)


queue_router = APIRouter(tags=["queue"])


@queue_router.get("/queue")
def queue_status(
    service: Service, principal: Annotated[Principal, Depends(require("queue:read"))]
) -> dict[str, Any]:
    """Job counts, the age of the oldest waiting job, and your recently dead-lettered jobs."""
    return service.queue_stats(principal.tenant)
