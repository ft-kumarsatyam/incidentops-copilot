"""Request-id, body-size, rate-limit and access-log middleware."""

import hashlib
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from incidentops.api.ratelimit import build_rate_limiter
from incidentops.core.config import Settings
from incidentops.core.logging import bound_context, get_logger
from incidentops.core.metrics import Metrics

log = get_logger(__name__)
_EXEMPT_PREFIXES = ("/health", "/ready", "/metrics", "/assets")


def _client_key(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    credential = request.headers.get("authorization", "") + request.headers.get("x-api-key", "")
    return f"{host}:{hashlib.sha256(credential.encode()).hexdigest()[:8]}"


def install_middleware(app: FastAPI, settings: Settings, metrics: Metrics | None = None) -> None:
    metrics = metrics or Metrics(False)
    limiter = build_rate_limiter(settings)
    app.state.limiter = limiter

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        started = time.perf_counter()
        with bound_context(request_id=request_id):
            response: Response
            declared = request.headers.get("content-length", "0")
            if declared.isdigit() and int(declared) > settings.max_body_bytes:
                response = JSONResponse({"error": "payload_too_large"}, status_code=413)
            elif not request.url.path.startswith(_EXEMPT_PREFIXES) and not await limiter.hit(
                _client_key(request)
            ):
                metrics.rate_limited()
                response = JSONResponse(
                    {"error": "rate_limited"}, status_code=429, headers={"Retry-After": "60"}
                )
            else:
                response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            elapsed = time.perf_counter() - started
            if not request.url.path.startswith(("/assets", "/metrics")):
                route = getattr(request.scope.get("route"), "path", None)
                metrics.http(request.method, route or "unmatched", response.status_code, elapsed)
            if not request.url.path.startswith("/assets"):
                log.info(
                    "http.request",
                    method=request.method,
                    path=request.url.path,
                    status=response.status_code,
                    duration_ms=round(elapsed * 1000, 1),
                )
            return response
