"""FastAPI dependencies: auth, service access and request metadata."""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Request

from incidentops.core.config import Settings
from incidentops.core.logging import get_logger
from incidentops.core.signing import SignatureError
from incidentops.core.signing import verify as verify_signature
from incidentops.persistence.audit import SYSTEM_TENANT
from incidentops.security import Authenticator, AuthError, Principal
from incidentops.workflows.incident.service import IncidentService

log = get_logger(__name__)


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_service(request: Request) -> IncidentService:
    service: IncidentService = request.app.state.service
    return service


def get_authenticator(request: Request) -> Authenticator:
    authenticator: Authenticator = request.app.state.authenticator
    return authenticator


def _audit_quietly(
    service: IncidentService,
    event: str,
    actor: str,
    detail: dict[str, Any],
    tenant: str,
) -> None:
    """Record a security event. Never let a logging failure change the response."""
    try:
        service.repo.add_audit("", event, actor, detail, tenant=tenant)
    except Exception as error:
        log.error("audit.write_failed", event=event, error=type(error).__name__)


def get_principal(
    request: Request,
    authenticator: Annotated[Authenticator, Depends(get_authenticator)],
    service: Annotated[IncidentService, Depends(get_service)],
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
    x_actor: Annotated[str | None, Header()] = None,
) -> Principal:
    """Who is calling. Failures are audited (without the credential) and answered with a 401."""
    try:
        principal = authenticator.authenticate(
            authorization=authorization, api_key=x_api_key, actor_header=x_actor
        )
    except AuthError as error:
        _audit_quietly(
            service,
            "auth:denied",
            "anonymous",
            {
                "reason": error.reason,
                "path": request.url.path,
                "method": request.method,
                "client": request.client.host if request.client else "unknown",
                **error.detail,
            },
            SYSTEM_TENANT,
        )
        raise HTTPException(
            status_code=401,
            detail="invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error
    request.state.principal = principal
    return principal


def require(permission: str) -> Callable[..., Principal]:
    """Dependency factory: authenticate, then insist on a permission (403 and audit if lacking)."""

    def dependency(
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
        service: Annotated[IncidentService, Depends(get_service)],
    ) -> Principal:
        if not principal.can(permission):
            _audit_quietly(
                service,
                "auth:forbidden",
                principal.actor,
                {
                    "permission": permission,
                    "path": request.url.path,
                    "method": request.method,
                    "roles": sorted(principal.roles),
                },
                principal.tenant,
            )
            raise HTTPException(status_code=403, detail=f"requires the {permission!r} permission")
        return principal

    return dependency


async def verify_inbound_signature(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    x_incidentops_timestamp: Annotated[str | None, Header()] = None,
    x_incidentops_signature: Annotated[str | None, Header()] = None,
) -> None:
    """When INBOUND_SIGNING_SECRET is set, require a fresh HMAC signature over the raw body."""
    secret = settings.inbound_signing_secret
    if not secret:
        return
    try:
        verify_signature(
            await request.body(),
            secret,
            timestamp=x_incidentops_timestamp,
            signature=x_incidentops_signature,
            tolerance=settings.signature_tolerance_seconds,
        )
    except SignatureError as error:
        raise HTTPException(status_code=401, detail=f"invalid signature: {error}") from error
