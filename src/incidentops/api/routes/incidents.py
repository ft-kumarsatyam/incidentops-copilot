"""Incident endpoints."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Response
from pydantic import BaseModel, Field

from incidentops.api.deps import authenticate, get_actor, get_service
from incidentops.workflows.incident.service import IncidentService

RUN_ID = r"^[A-Za-z0-9._:-]{1,128}$"

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
    symptoms: list[str] = Field(default_factory=list, max_length=20)
    monitoring: dict[str, Any] = Field(default_factory=dict)
    github: dict[str, Any] = Field(default_factory=dict)


class DecisionRequest(BaseModel):
    idempotency_key: str = Field(min_length=1)
    decision: Literal["approved", "rejected"]


@router.get("")
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


@router.post("")
def create_incident(request: IncidentRequest, service: Service, actor: Actor) -> dict[str, Any]:
    return service.start(request.model_dump(exclude_none=True), actor=actor)


@router.get("/{run_id}")
def get_incident(run_id: RunIdPath, service: Service) -> dict[str, Any]:
    return service.get(run_id)


@router.get("/{run_id}/audit")
def get_audit(run_id: RunIdPath, service: Service) -> list[dict[str, Any]]:
    service.get(run_id)
    return service.audit(run_id)


@router.post("/{run_id}/decision")
def decide(
    run_id: RunIdPath, request: DecisionRequest, service: Service, actor: Actor
) -> dict[str, Any]:
    return service.decide(run_id, request.idempotency_key, request.decision, actor=actor)


@router.post("/{run_id}/retry-dispatch")
def retry_dispatch(run_id: RunIdPath, service: Service, actor: Actor) -> dict[str, Any]:
    return service.retry_dispatch(run_id, actor=actor)
