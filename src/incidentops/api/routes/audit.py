"""Audit log: search, export and chain verification (admins, scoped to their own tenant)."""

import json
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from incidentops.api.deps import get_service, require
from incidentops.security import Principal
from incidentops.workflows.incident.service import IncidentService

router = APIRouter(prefix="/audit", tags=["audit"])
Service = Annotated[IncidentService, Depends(get_service)]
Auditor = Annotated[Principal, Depends(require("audit:search"))]
_PAGE = 500


class AuditRecord(BaseModel):
    seq: int
    tenant: str
    run_id: str
    event: str
    actor: str
    detail: dict[str, Any]
    at: str
    hash: str


class ChainStatus(BaseModel):
    tenant: str
    ok: bool
    checked: int
    head_seq: int
    error: str | None = None
    bad_seq: int | None = None


@router.get("", response_model=list[AuditRecord])
def search_audit(
    service: Service,
    principal: Auditor,
    response: Response,
    run_id: Annotated[str | None, Query(max_length=128)] = None,
    event: Annotated[str | None, Query(max_length=64, description="e.g. decision or auth")] = None,
    actor: Annotated[str | None, Query(max_length=128)] = None,
    since: Annotated[str | None, Query(description="ISO timestamp, inclusive")] = None,
    until: Annotated[str | None, Query(description="ISO timestamp, exclusive")] = None,
    limit: Annotated[int, Query(ge=1, le=_PAGE)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    rows, total = service.audit_search(
        tenant=principal.tenant,
        run_id=run_id,
        event=event,
        actor=actor,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    response.headers["X-Total-Count"] = str(total)
    return rows


@router.get("/verify", response_model=ChainStatus)
def verify_audit(service: Service, principal: Auditor) -> dict[str, Any]:
    """Recompute the hash chain. ``ok: false`` names the first entry that does not check out."""
    return service.audit_verify(principal.tenant).as_dict()


@router.get("/export", response_class=StreamingResponse)
def export_audit(service: Service, principal: Auditor) -> StreamingResponse:
    """Every entry for the tenant as NDJSON, ending with a chain-verification line. Ship this
    to storage the database operator cannot write to, to anchor the log outside the database."""
    service.repo.add_audit("", "audit:exported", principal.actor, {}, tenant=principal.tenant)

    def lines() -> Iterator[str]:
        offset = 0
        while True:
            rows, _ = service.audit_search(tenant=principal.tenant, limit=_PAGE, offset=offset)
            for row in rows:
                yield json.dumps({"type": "entry", **row}, default=str) + "\n"
            if len(rows) < _PAGE:
                break
            offset += _PAGE
        yield (
            json.dumps({"type": "chain", **service.audit_verify(principal.tenant).as_dict()}) + "\n"
        )

    return StreamingResponse(
        lines(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="audit-{principal.tenant}.ndjson"'},
    )
