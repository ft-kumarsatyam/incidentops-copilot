"""Incident endpoints."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from incidentops.api.deps import authenticate, get_actor, get_service, verify_inbound_signature
from incidentops.api.events import event_stream
from incidentops.workflows.incident.service import IncidentService

RUN_ID = r"^[A-Za-z0-9._:-]{1,128}$"
Symptom = Annotated[str, StringConstraints(max_length=500)]

router = APIRouter(prefix="/incidents", tags=["incidents"], dependencies=[Depends(authenticate)])
Service = Annotated[IncidentService, Depends(get_service)]
Actor = Annotated[str, Depends(get_actor)]
RunIdPath = Annotated[str, Path(pattern=RUN_ID)]


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
    response: Response,
    status: Annotated[list[str] | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    items, total = service.search(statuses=status, query=q, limit=limit, offset=offset)
    response.headers["X-Total-Count"] = str(total)
    return items


@router.post("", response_model=IncidentOut, dependencies=[Depends(verify_inbound_signature)])
def create_incident(request: IncidentRequest, service: Service, actor: Actor) -> dict[str, Any]:
    return service.start(request.model_dump(exclude_none=True), actor=actor)


@router.get("/stream", response_class=StreamingResponse)
async def stream(request: Request, service: Service) -> StreamingResponse:
    """Server-Sent Events: one `incident` event whenever any run changes."""
    return StreamingResponse(
        event_stream(service.events, request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{run_id}", response_model=IncidentOut)
def get_incident(run_id: RunIdPath, service: Service) -> dict[str, Any]:
    return service.get(run_id)


@router.get("/{run_id}/audit", response_model=list[AuditEntry])
def get_audit(run_id: RunIdPath, service: Service) -> list[dict[str, Any]]:
    service.get(run_id)
    return service.audit(run_id)


@router.post("/{run_id}/decision", response_model=IncidentOut)
def decide(
    run_id: RunIdPath, request: DecisionRequest, service: Service, actor: Actor
) -> dict[str, Any]:
    return service.decide(
        run_id,
        request.idempotency_key,
        request.decision,
        approved_actions=request.approved_actions,
        actor=actor,
    )


@router.post("/{run_id}/retry-dispatch", response_model=IncidentOut)
def retry_dispatch(run_id: RunIdPath, service: Service, actor: Actor) -> dict[str, Any]:
    return service.retry_dispatch(run_id, actor=actor)
