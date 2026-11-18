"""Prometheus scrape endpoint. Registered only when METRICS_ENABLED=true."""

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from incidentops.api.deps import get_settings
from incidentops.core.config import Settings
from incidentops.core.metrics import CONTENT_TYPE

router = APIRouter(tags=["metrics"])


def _authorize(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    expected = settings.metrics_token
    if expected is None:
        # Startup refuses an unauthenticated /metrics outside dev, so this is dev only.
        return
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="invalid metrics token")


@router.get("/metrics", include_in_schema=False, dependencies=[Depends(_authorize)])
def metrics(request: Request) -> Response:
    return Response(request.app.state.metrics.render(), media_type=CONTENT_TYPE)
