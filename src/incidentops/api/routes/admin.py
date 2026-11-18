"""Who am I, and API key management (admins manage keys for their own tenant)."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from incidentops.api.deps import get_principal, get_service, require
from incidentops.security import ROLES, Principal
from incidentops.security.apikeys import issue_key
from incidentops.workflows.incident.service import IncidentService

router = APIRouter(tags=["identity"])
Service = Annotated[IncidentService, Depends(get_service)]
Admin = Annotated[Principal, Depends(require("keys:manage"))]
KeyId = Annotated[str, Path(pattern=r"^[0-9a-f]{12}$")]


class KeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._ -]+$")
    roles: list[Literal["viewer", "responder", "approver", "admin"]] = Field(min_length=1)
    expires_days: int | None = Field(default=None, ge=1, le=3650)


class KeyOut(BaseModel):
    id: str
    name: str
    tenant: str
    roles: list[str]
    created_by: str
    created_at: str
    expires_at: str | None = None
    revoked_at: str | None = None
    last_used_at: str | None = None


class KeyCreated(BaseModel):
    key: KeyOut
    token: str = Field(description="Shown once. Send it as `Authorization: Bearer <token>`.")


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k != "secret_hash"}


@router.get("/me")
def me(principal: Annotated[Principal, Depends(get_principal)]) -> dict[str, object]:
    """The authenticated identity, its roles and what it may do (the UI uses this to hide
    actions the caller cannot take)."""
    return principal.describe() | {"all_roles": list(ROLES)}


@router.post("/admin/keys", response_model=KeyCreated, status_code=201)
def create_key(request: KeyRequest, service: Service, principal: Admin) -> dict[str, Any]:
    stored, token = issue_key(
        service.repo,
        name=request.name,
        tenant=principal.tenant,
        roles=list(request.roles),
        created_by=principal.actor,
        expires_days=request.expires_days,
    )
    service.repo.add_audit(
        "",
        "key:created",
        principal.actor,
        {"key_id": stored["id"], "name": request.name, "roles": request.roles},
        tenant=principal.tenant,
    )
    return {"key": stored, "token": token}


@router.get("/admin/keys", response_model=list[KeyOut])
def list_keys(service: Service, principal: Admin) -> list[dict[str, Any]]:
    return [_public(row) for row in service.repo.key_list(principal.tenant)]


@router.delete("/admin/keys/{key_id}", status_code=204)
def revoke_key(key_id: KeyId, service: Service, principal: Admin) -> None:
    if not service.repo.key_revoke(key_id, principal.tenant):
        raise HTTPException(status_code=404, detail="key not found or already revoked")
    service.repo.add_audit(
        "", "key:revoked", principal.actor, {"key_id": key_id}, tenant=principal.tenant
    )
