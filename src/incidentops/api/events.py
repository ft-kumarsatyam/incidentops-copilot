"""Pub/sub that feeds the live dashboard stream (Server-Sent Events).

``EventBus`` fans events out to the SSE clients connected to this process. ``RedisEventBus``
routes every event through a Redis channel first, so a client connected to one replica sees
changes made on another. Callers only use ``publish`` and ``subscribe`` either way.
"""

import asyncio
import json
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from incidentops.core.config import Settings
from incidentops.core.logging import get_logger

log = get_logger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]]]] = (
            set()
        )

    def publish(self, event: dict[str, Any]) -> None:
        """Safe to call from any thread (the workflow runs in FastAPI's threadpool)."""
        self._deliver(event)

    def _deliver(self, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:  # that loop has shut down
                self._discard(loop, queue)

    def _discard(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[Any]) -> None:
        with self._lock:
            self._subscribers.discard((loop, queue))

    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        loop = asyncio.get_running_loop()
        with self._lock:
            self._subscribers.add((loop, queue))
        try:
            yield queue
        finally:
            self._discard(loop, queue)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        return None


class RedisEventBus(EventBus):
    """Publishes to Redis; a listener thread delivers every message to local subscribers.

    There is one delivery path, through Redis, so a replica sees its own events exactly once.
    The database is the source of truth and the stream is a live hint, so if Redis is down
    ``publish`` delivers locally instead of raising, and the listener reconnects with backoff.
    """

    def __init__(self, url: str, *, prefix: str = "incidentops") -> None:
        super().__init__()
        try:
            import redis
        except ImportError as error:  # pragma: no cover - only without the extra
            raise RuntimeError(
                "REDIS_URL needs the Redis extra: pip install 'incidentops[redis]'"
            ) from error
        self._redis: Any = redis
        self._url = url
        self._channel = f"{prefix}:events"
        self._client: Any = redis.Redis.from_url(
            url, socket_timeout=2.0, socket_connect_timeout=2.0
        )
        self._stop = threading.Event()
        self.listening = threading.Event()
        self._thread = threading.Thread(target=self._listen, name="event-bus", daemon=True)
        self._thread.start()

    def publish(self, event: dict[str, Any]) -> None:
        try:
            self._client.publish(self._channel, json.dumps(event, default=str))
        except Exception as error:  # redis errors, timeouts, refused connections
            log.warning("events.publish_failed", error=type(error).__name__)
            self._deliver(event)

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:
            return False

    def _listen(self) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            # A dedicated connection without a read timeout: an idle subscription is normal.
            client: Any = self._redis.Redis.from_url(
                self._url, socket_connect_timeout=2.0, health_check_interval=15
            )
            pubsub = client.pubsub()
            try:
                pubsub.subscribe(self._channel)
                while not self._stop.is_set():
                    message = pubsub.get_message(timeout=1.0)
                    if not message:
                        continue
                    if message["type"] == "subscribe":
                        self.listening.set()
                        backoff = 0.5
                    elif message["type"] == "message":
                        self._relay(message["data"])
            except Exception as error:
                self.listening.clear()
                if not self._stop.is_set():
                    log.warning("events.listener_lost", error=type(error).__name__)
                    self._stop.wait(backoff)
                    backoff = min(backoff * 2, 10.0)
            finally:
                try:
                    pubsub.close()
                    client.close()
                except Exception:
                    pass

    def _relay(self, raw: bytes | str) -> None:
        try:
            event = json.loads(raw)
        except ValueError:
            log.warning("events.malformed_message")
            return
        if isinstance(event, dict):
            self._deliver(event)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)
        try:
            self._client.close()
        except Exception:
            pass


def build_event_bus(settings: Settings) -> EventBus:
    if settings.redis_url:
        return RedisEventBus(settings.redis_url, prefix=settings.redis_prefix)
    return EventBus()


def sse_frame(event: dict[str, Any]) -> str:
    return f"event: {event.get('type', 'message')}\ndata: {json.dumps(event, default=str)}\n\n"


async def event_stream(
    bus: EventBus,
    is_disconnected: Callable[[], Awaitable[bool]],
    heartbeat_seconds: float = 15.0,
) -> AsyncIterator[str]:
    """Yield SSE frames until the client goes away. Heartbeats keep proxies from closing it."""
    async for queue in bus.subscribe():
        yield sse_frame({"type": "ready"})
        while not await is_disconnected():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue
            yield sse_frame(event)
