"""In-process pub/sub that feeds the live dashboard stream (Server-Sent Events).

Single-process by design. With several API replicas, replace the bus with Redis pub/sub or
Postgres LISTEN/NOTIFY; the rest of the code only sees ``publish`` and ``subscribe``.
"""

import asyncio
import json
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]]]] = (
            set()
        )

    def publish(self, event: dict[str, Any]) -> None:
        """Safe to call from any thread (the workflow runs in FastAPI's threadpool)."""
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
