"""Request rate limiting: in-process by default, shared across replicas with Redis.

Both limiters expose ``async hit(key)``. The Redis one keeps one sorted set per client and does
the trim, count and insert in a single Lua script, so concurrent replicas cannot race past the
limit. It uses Redis' own clock, so replica clock skew does not matter. If Redis is unreachable
it falls back to a per-process limiter rather than failing requests: the limit gets looser
(per replica instead of global) but the API stays up.
"""

import time
import uuid
from collections import defaultdict, deque
from typing import Any, Protocol

from incidentops.core.config import Settings
from incidentops.core.logging import get_logger

log = get_logger(__name__)

WINDOW_SECONDS = 60
_LOG_EVERY_SECONDS = 30.0

_SLIDING_WINDOW = """
local t = redis.call('TIME')
local now = t[1] * 1000 + math.floor(t[2] / 1000)
local window = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now - window)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then
    return 0
end
redis.call('ZADD', KEYS[1], now, ARGV[3])
redis.call('PEXPIRE', KEYS[1], window)
return 1
"""


class Limiter(Protocol):
    async def hit(self, key: str) -> bool: ...

    async def aclose(self) -> None: ...


class RateLimiter:
    """In-process sliding window. Correct for one replica; see RedisRateLimiter for many."""

    def __init__(self, limit_per_minute: int) -> None:
        self.limit = limit_per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, now: float | None = None) -> bool:
        if self.limit <= 0:
            return True
        now = time.monotonic() if now is None else now
        window = self._hits[key]
        while window and now - window[0] > WINDOW_SECONDS:
            window.popleft()
        if len(window) >= self.limit:
            return False
        window.append(now)
        return True

    async def hit(self, key: str) -> bool:
        return self.allow(key)

    async def aclose(self) -> None:
        return None


class RedisRateLimiter:
    def __init__(self, url: str, limit_per_minute: int, *, prefix: str = "incidentops") -> None:
        try:
            from redis import asyncio as aioredis
        except ImportError as error:  # pragma: no cover - only without the extra
            raise RuntimeError(
                "REDIS_URL needs the Redis extra: pip install 'incidentops[redis]'"
            ) from error
        self.limit = limit_per_minute
        self._prefix = prefix
        self._client: Any = aioredis.Redis.from_url(
            url, socket_timeout=1.0, socket_connect_timeout=1.0
        )
        self._script = self._client.register_script(_SLIDING_WINDOW)
        self._fallback = RateLimiter(limit_per_minute)
        self._last_logged = 0.0

    async def hit(self, key: str) -> bool:
        if self.limit <= 0:
            return True
        try:
            allowed = await self._script(
                keys=[f"{self._prefix}:rl:{key}"],
                args=[self.limit, WINDOW_SECONDS * 1000, uuid.uuid4().hex],
            )
        except Exception as error:  # redis errors, timeouts, refused connections
            self._log_outage(error)
            return self._fallback.allow(key)
        return bool(allowed == 1)

    def _log_outage(self, error: Exception) -> None:
        now = time.monotonic()
        if now - self._last_logged >= _LOG_EVERY_SECONDS:
            self._last_logged = now
            log.warning("ratelimit.redis_unavailable", error=type(error).__name__)

    async def aclose(self) -> None:
        await self._client.aclose()


def build_rate_limiter(settings: Settings) -> Limiter:
    if settings.redis_url:
        return RedisRateLimiter(
            settings.redis_url, settings.rate_limit_per_minute, prefix=settings.redis_prefix
        )
    return RateLimiter(settings.rate_limit_per_minute)
