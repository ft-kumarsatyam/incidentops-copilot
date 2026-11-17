"""Request-id, body-size, rate-limit and access-log middleware."""

import hashlib
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from incidentops.core.config import Settings
from incidentops.core.logging import bound_context, get_logger

log = get_logger(__name__)
_EXEMPT_PREFIXES = ("/health", "/ready", "/assets")


class RateLimiter:
    """In-process sliding window. Use a shared store (Redis) if you run multiple replicas."""

    def __init__(self, limit_per_minute: int) -> None:
        self.limit = limit_per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, now: float | None = None) -> bool:
        if self.limit <= 0:
            return True
        now = time.monotonic() if now is None else now
        window = self._hits[key]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.limit:
            return False
        window.append(now)
        return True


def _client_key(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    api_key = request.headers.get("x-api-key", "")
    return f"{host}:{hashlib.sha256(api_key.encode()).hexdigest()[:8]}"


def install_middleware(app: FastAPI, settings: Settings) -> None:
    limiter = RateLimiter(settings.rate_limit_per_minute)

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
            elif not request.url.path.startswith(_EXEMPT_PREFIXES) and not limiter.allow(
                _client_key(request)
            ):
                response = JSONResponse(
                    {"error": "rate_limited"}, status_code=429, headers={"Retry-After": "60"}
                )
            else:
                response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            if not request.url.path.startswith("/assets"):
                log.info(
                    "http.request",
                    method=request.method,
                    path=request.url.path,
                    status=response.status_code,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                )
            return response
