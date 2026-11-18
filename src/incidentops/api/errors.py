"""Map domain errors to consistent JSON error responses."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from incidentops.core.errors import (
    AccessDeniedError,
    IncidentConflictError,
    IncidentNotFoundError,
)
from incidentops.core.logging import get_logger

log = get_logger(__name__)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(IncidentNotFoundError)
    async def not_found(_: Request, error: IncidentNotFoundError) -> JSONResponse:
        return JSONResponse({"detail": "run not found"}, status_code=404)

    @app.exception_handler(IncidentConflictError)
    async def conflict(_: Request, error: IncidentConflictError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(AccessDeniedError)
    async def denied(_: Request, error: AccessDeniedError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=403)

    @app.exception_handler(Exception)
    async def unexpected(_: Request, error: Exception) -> JSONResponse:
        log.error("http.unhandled", error=repr(error), exc_info=True)
        return JSONResponse({"detail": "internal server error"}, status_code=500)
