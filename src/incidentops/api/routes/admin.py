"""Who am I, and API key management (admins manage keys for their own tenant)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from incidentops.api.deps import get_principal, get_service, require
from incidentops.security import PERMISSIONS, ROLES, Principal
from incidentops.security.apikeys import issue_key
from incidentops.workflows.incident.service import IncidentService

router = APIRouter(tags=["identity"])
Service = Annotated[IncidentService, Depends(get_service)]
Admin = Annotated[Principal, Depends(require("keys:manage"))]
RoleAdmin = Annotated[Principal, Depends(require("roles:manage"))]
RoleName = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,64}$")]
KeyId = Annotated[str, Path(pattern=r"^[0-9a-f]{12}$")]


class KeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._ -]+$")
    roles: list[Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")]] = Field(
        min_length=1,
        description="Built-in roles and/or custom roles defined in your tenant.",
    )
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
    try:
        stored, token = issue_key(
            service.repo,
            name=request.name,
            tenant=principal.tenant,
            roles=list(request.roles),
            created_by=principal.actor,
            expires_days=request.expires_days,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
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


class RoleRequest(BaseModel):
    description: str = Field(default="", max_length=200)
    permissions: list[str] = Field(min_length=1, max_length=len(PERMISSIONS))
    services: list[Annotated[str, Field(pattern=r"^[A-Za-z0-9._*?-]{1,128}$")]] = Field(
        default_factory=list,
        max_length=50,
        description="Glob patterns such as 'checkout-*'. Empty means every service.",
    )


class RoleOut(BaseModel):
    name: str
    description: str
    permissions: list[str]
    services: list[str]
    created_by: str
    created_at: str
    updated_at: str


class RolesOut(BaseModel):
    built_in: list[dict[str, Any]]
    custom: list[RoleOut]
    permissions: dict[str, str] = Field(
        description="Every permission and the lowest built-in role that has it."
    )


def _built_in() -> list[dict[str, Any]]:
    return [
        {
            "name": role,
            "permissions": [p for p, lowest in PERMISSIONS.items() if ROLES.index(lowest) <= index],
        }
        for index, role in enumerate(ROLES)
    ]


@router.get("/admin/roles", response_model=RolesOut)
def list_roles(service: Service, principal: RoleAdmin) -> dict[str, Any]:
    return {
        "built_in": _built_in(),
        "custom": service.repo.role_list(principal.tenant),
        "permissions": PERMISSIONS,
    }


@router.put("/admin/roles/{name}", response_model=RoleOut)
def put_role(
    name: RoleName, request: RoleRequest, service: Service, principal: RoleAdmin
) -> dict[str, Any]:
    """Create a custom role, or replace one. Built-in role names cannot be used."""
    if name in ROLES:
        raise HTTPException(status_code=422, detail=f"{name!r} is a built-in role")
    unknown = sorted(set(request.permissions) - set(PERMISSIONS))
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown permission(s): {', '.join(unknown)}")
    existing = {row["name"] for row in service.repo.role_list(principal.tenant)}
    stored = service.repo.role_upsert(
        principal.tenant,
        name,
        description=request.description,
        permissions=sorted(set(request.permissions)),
        services=request.services,
        created_by=principal.actor,
    )
    service.repo.add_audit(
        "",
        "role:updated" if name in existing else "role:created",
        principal.actor,
        {
            "role": name,
            "permissions": stored["permissions"],
            "services": stored["services"],
        },
        tenant=principal.tenant,
    )
    return stored


@router.delete("/admin/roles/{name}", status_code=204)
def delete_role(name: RoleName, service: Service, principal: RoleAdmin) -> None:
    """Delete a custom role. Keys and tokens that carry its name keep the name but lose what it
    granted, immediately."""
    if not service.repo.role_delete(principal.tenant, name):
        raise HTTPException(status_code=404, detail="role not found")
    service.repo.add_audit(
        "", "role:deleted", principal.actor, {"role": name}, tenant=principal.tenant
    )
