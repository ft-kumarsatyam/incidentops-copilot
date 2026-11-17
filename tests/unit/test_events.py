import asyncio
import json
import threading
from typing import Any

from incidentops.api.events import EventBus, event_stream, sse_frame


def test_sse_frame_format() -> None:
    frame = sse_frame({"type": "incident", "run_id": "a"})

    assert frame.startswith("event: incident\ndata: ")
    assert frame.endswith("\n\n")
    assert json.loads(frame.split("data: ")[1]) == {"type": "incident", "run_id": "a"}


async def _collect(bus: EventBus, count: int, heartbeat: float = 0.05) -> list[str]:
    frames: list[str] = []
    disconnected = False

    async def is_disconnected() -> bool:
        return disconnected

    async for frame in event_stream(bus, is_disconnected, heartbeat):
        frames.append(frame)
        if len(frames) >= count:
            disconnected = True
    return frames


def test_stream_delivers_events_published_from_another_thread() -> None:
    bus = EventBus()

    async def scenario() -> list[str]:
        collector = asyncio.create_task(_collect(bus, 3))
        while bus.subscriber_count == 0:
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.02)  # let the stream emit its ready frame
        worker = threading.Thread(target=lambda: bus.publish({"type": "incident", "run_id": "x"}))
        worker.start()
        worker.join()
        return await asyncio.wait_for(collector, 2)

    frames = asyncio.run(scenario())

    assert frames[0] == sse_frame({"type": "ready"})
    assert any('"run_id": "x"' in frame for frame in frames)


def test_idle_stream_sends_keep_alives_and_cleans_up() -> None:
    bus = EventBus()

    frames: list[Any] = asyncio.run(_collect(bus, 2))

    assert frames[1] == ": keep-alive\n\n"
    assert bus.subscriber_count == 0
