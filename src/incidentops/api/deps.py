"""FastAPI dependencies: auth, service access and request metadata."""

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from incidentops.core.config import Settings
from incidentops.core.signing import SignatureError
from incidentops.core.signing import verify as verify_signature
from incidentops.workflows.incident.service import IncidentService


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_service(request: Request) -> IncidentService:
    service: IncidentService = request.app.state.service
    return service


def authenticate(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    expected = settings.incidentops_api_key
    if expected is None:
        # Startup refuses to run without a key outside dev, so this only happens in dev.
        return
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="invalid API key")


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


def get_actor(x_actor: Annotated[str | None, Header()] = None) -> str:
    """Who is acting, for the audit log. Trusted only as far as the API key is."""
    cleaned = "".join(ch for ch in (x_actor or "") if ch.isprintable())[:64].strip()
    return cleaned or "api"
