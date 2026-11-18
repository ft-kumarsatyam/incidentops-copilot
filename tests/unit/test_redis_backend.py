"""Redis-backed event bus and rate limiter, tested the way replicas use them: several instances
sharing one Redis. Skipped unless TEST_REDIS_URL is set."""

import asyncio
import json
import threading
import time
from collections.abc import Iterator
from contextlib import aclosing
from pathlib import Path
from typing import Any

import pytest
import redis
from fastapi.testclient import TestClient

from incidentops.api import ratelimit
from incidentops.api.app import create_app
from incidentops.api.events import EventBus, RedisEventBus, build_event_bus
from incidentops.api.ratelimit import RateLimiter, RedisRateLimiter, build_rate_limiter
from incidentops.core.config import Settings
from incidentops.workflows.incident import IncidentService
from tests.conftest import load_fixture

DEAD = "redis://127.0.0.1:1/0"  # nothing listens here: connections are refused immediately


@pytest.fixture
def buses(redis_url: str, redis_prefix: str) -> Iterator[list[RedisEventBus]]:
    created: list[RedisEventBus] = []
    yield created
    for bus in created:
        bus.close()


def _bus(buses: list[RedisEventBus], url: str, prefix: str) -> RedisEventBus:
    bus = RedisEventBus(url, prefix=prefix)
    buses.append(bus)
    assert bus.listening.wait(5), "listener never subscribed"
    return bus


async def _publish_in_thread(bus: EventBus, event: dict[str, Any]) -> None:
    # The workflow publishes from FastAPI's threadpool, never from the event loop.
    await asyncio.get_running_loop().run_in_executor(None, bus.publish, event)


# -- event bus ------------------------------------------------------------------------------
def test_an_event_published_on_one_replica_reaches_a_client_on_another(
    buses: list[RedisEventBus], redis_url: str, redis_prefix: str
) -> None:
    replica_a = _bus(buses, redis_url, redis_prefix)
    replica_b = _bus(buses, redis_url, redis_prefix)

    async def scenario() -> dict[str, Any]:
        async with aclosing(replica_b.subscribe()) as subscription:
            queue = await anext(subscription)
            await _publish_in_thread(replica_a, {"type": "incident", "run_id": "x"})
            return await asyncio.wait_for(queue.get(), 3)

    assert asyncio.run(scenario()) == {"type": "incident", "run_id": "x"}


def test_the_publishing_replica_sees_its_own_event_exactly_once(
    buses: list[RedisEventBus], redis_url: str, redis_prefix: str
) -> None:
    replica = _bus(buses, redis_url, redis_prefix)

    async def scenario() -> list[dict[str, Any]]:
        async with aclosing(replica.subscribe()) as subscription:
            queue = await anext(subscription)
            await _publish_in_thread(replica, {"type": "incident", "run_id": "once"})
            await asyncio.sleep(0.4)  # long enough for a duplicate to show up
            return [queue.get_nowait() for _ in range(queue.qsize())]

    assert asyncio.run(scenario()) == [{"type": "incident", "run_id": "once"}]


def test_replicas_with_different_prefixes_do_not_see_each_other(
    buses: list[RedisEventBus], redis_url: str, redis_prefix: str
) -> None:
    ours = _bus(buses, redis_url, redis_prefix)
    other_tenant = _bus(buses, redis_url, redis_prefix + "-other")

    async def scenario() -> int:
        async with aclosing(ours.subscribe()) as subscription:
            queue = await anext(subscription)
            await _publish_in_thread(other_tenant, {"type": "incident", "run_id": "theirs"})
            await asyncio.sleep(0.4)
            return queue.qsize()

    assert asyncio.run(scenario()) == 0


def test_a_malformed_message_is_skipped_and_the_stream_keeps_working(
    buses: list[RedisEventBus], redis_url: str, redis_prefix: str
) -> None:
    replica = _bus(buses, redis_url, redis_prefix)
    raw = redis.Redis.from_url(redis_url)
    channel = f"{redis_prefix}:events"

    async def scenario() -> dict[str, Any]:
        async with aclosing(replica.subscribe()) as subscription:
            queue = await anext(subscription)
            raw.publish(channel, b"not json {")
            raw.publish(channel, json.dumps(["not", "an", "object"]))
            await _publish_in_thread(replica, {"type": "incident", "run_id": "after"})
            return await asyncio.wait_for(queue.get(), 3)

    assert asyncio.run(scenario())["run_id"] == "after"


def test_publish_falls_back_to_local_delivery_when_redis_is_down() -> None:
    bus = RedisEventBus(DEAD, prefix="down")
    try:

        async def scenario() -> dict[str, Any]:
            async with aclosing(bus.subscribe()) as subscription:
                queue = await anext(subscription)
                await _publish_in_thread(bus, {"type": "incident", "run_id": "local"})
                return await asyncio.wait_for(queue.get(), 3)

        assert asyncio.run(scenario())["run_id"] == "local"
        assert bus.ping() is False
    finally:
        started = time.monotonic()
        bus.close()
    assert time.monotonic() - started < 5, "close() must not hang while Redis is unreachable"


def test_the_listener_reconnects_after_redis_comes_back(
    buses: list[RedisEventBus], redis_url: str, redis_prefix: str
) -> None:
    replica = _bus(buses, redis_url, redis_prefix)
    raw = redis.Redis.from_url(redis_url)
    # Drop every subscriber connection, as a Redis restart or failover would.
    raw.execute_command("CLIENT", "KILL", "TYPE", "pubsub")

    deadline = time.monotonic() + 8
    # First the listener must notice the loss, then it must resubscribe. Pub/sub drops anything
    # published in between, so publishing before the second step would be a test race.
    while replica.listening.is_set():
        assert time.monotonic() < deadline, "listener never noticed the dropped connection"
        time.sleep(0.02)
    while not replica.listening.wait(0.1):
        assert time.monotonic() < deadline, "listener did not resubscribe"

    async def scenario() -> dict[str, Any]:
        async with aclosing(replica.subscribe()) as subscription:
            queue = await anext(subscription)
            await _publish_in_thread(replica, {"type": "incident", "run_id": "back"})
            return await asyncio.wait_for(queue.get(), 3)

    assert asyncio.run(scenario())["run_id"] == "back"


def test_build_event_bus_follows_settings(settings: Settings, redis_url: str) -> None:
    assert type(build_event_bus(settings)) is EventBus
    bus = build_event_bus(settings.model_copy(update={"redis_url": redis_url}))
    try:
        assert isinstance(bus, RedisEventBus) and bus.ping()
    finally:
        bus.close()


# -- rate limiter ---------------------------------------------------------------------------
def test_replicas_share_one_rate_limit(redis_url: str, redis_prefix: str) -> None:
    async def scenario() -> list[bool]:
        a = RedisRateLimiter(redis_url, 3, prefix=redis_prefix)
        b = RedisRateLimiter(redis_url, 3, prefix=redis_prefix)
        try:
            return [await a.hit("c"), await b.hit("c"), await a.hit("c"), await b.hit("c")]
        finally:
            await a.aclose()
            await b.aclose()

    assert asyncio.run(scenario()) == [True, True, True, False]


def test_concurrent_requests_across_replicas_never_exceed_the_limit(
    redis_url: str, redis_prefix: str
) -> None:
    async def scenario() -> int:
        replicas = [RedisRateLimiter(redis_url, 10, prefix=redis_prefix) for _ in range(3)]
        try:
            results = await asyncio.gather(*(replicas[i % 3].hit("burst") for i in range(60)))
        finally:
            for replica in replicas:
                await replica.aclose()
        return sum(results)

    assert asyncio.run(scenario()) == 10


def test_clients_and_prefixes_have_separate_budgets(redis_url: str, redis_prefix: str) -> None:
    async def scenario() -> list[bool]:
        mine = RedisRateLimiter(redis_url, 1, prefix=redis_prefix)
        theirs = RedisRateLimiter(redis_url, 1, prefix=redis_prefix + "-other")
        try:
            return [
                await mine.hit("alice"),
                await mine.hit("alice"),  # over budget
                await mine.hit("bob"),  # a different client
                await theirs.hit("alice"),  # a different deployment
            ]
        finally:
            await mine.aclose()
            await theirs.aclose()

    assert asyncio.run(scenario()) == [True, False, True, True]


def test_the_window_slides(
    redis_url: str, redis_prefix: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ratelimit, "WINDOW_SECONDS", 1)

    async def scenario() -> list[bool]:
        limiter = RedisRateLimiter(redis_url, 1, prefix=redis_prefix)
        try:
            first, second = await limiter.hit("k"), await limiter.hit("k")
            await asyncio.sleep(1.2)
            return [first, second, await limiter.hit("k")]
        finally:
            await limiter.aclose()

    assert asyncio.run(scenario()) == [True, False, True]


def test_the_limiter_falls_back_to_local_counting_when_redis_is_down() -> None:
    async def scenario() -> list[bool]:
        limiter = RedisRateLimiter(DEAD, 2, prefix="down")
        try:
            return [await limiter.hit("k"), await limiter.hit("k"), await limiter.hit("k")]
        finally:
            await limiter.aclose()

    assert asyncio.run(scenario()) == [True, True, False]


def test_a_zero_limit_disables_limiting_without_touching_redis() -> None:
    async def scenario() -> bool:
        limiter = RedisRateLimiter(DEAD, 0, prefix="off")
        try:
            return all([await limiter.hit("k") for _ in range(50)])
        finally:
            await limiter.aclose()

    assert asyncio.run(scenario()) is True


def test_build_rate_limiter_follows_settings(settings: Settings, redis_url: str) -> None:
    assert type(build_rate_limiter(settings)) is RateLimiter
    shared = build_rate_limiter(settings.model_copy(update={"redis_url": redis_url}))
    assert isinstance(shared, RedisRateLimiter)
    asyncio.run(shared.aclose())


# -- through the HTTP API, as two replicas ---------------------------------------------------
def _replica_settings(settings: Settings, redis_url: str, prefix: str, **extra: Any) -> Settings:
    return settings.model_copy(
        update={
            "incidentops_api_key": "k",
            "redis_url": redis_url,
            "redis_prefix": prefix,
            **extra,
        }
    )


def test_the_http_rate_limit_is_global_across_replicas(
    settings: Settings, redis_url: str, redis_prefix: str
) -> None:
    config = _replica_settings(settings, redis_url, redis_prefix, rate_limit_per_minute=3)
    headers = {"X-API-Key": "k"}
    with TestClient(create_app(config)) as a, TestClient(create_app(config)) as b:
        statuses = [
            a.get("/incidents", headers=headers).status_code,
            b.get("/incidents", headers=headers).status_code,
            a.get("/incidents", headers=headers).status_code,
            b.get("/incidents", headers=headers).status_code,
        ]
        health = b.get("/health").status_code  # probes are never rate limited

    assert statuses == [200, 200, 200, 429]
    assert health == 200


def test_readiness_reports_redis_but_stays_ready_when_it_is_down(
    settings: Settings, redis_url: str, redis_prefix: str
) -> None:
    healthy = _replica_settings(settings, redis_url, redis_prefix)
    broken = _replica_settings(settings, DEAD, redis_prefix)

    with TestClient(create_app(healthy)) as client:
        assert client.get("/ready").json()["checks"]["redis"] == "ok"
    with TestClient(create_app(broken)) as client:
        response = client.get("/ready")

    assert response.status_code == 200  # Redis is an enhancement, not a hard dependency
    assert response.json()["checks"]["redis"] == "unavailable"


def test_a_run_started_on_one_replica_is_announced_to_clients_of_another(
    settings: Settings, redis_url: str, redis_prefix: str, tmp_path: Path
) -> None:
    config = _replica_settings(settings, redis_url, redis_prefix)
    replica_a, replica_b = IncidentService(config), IncidentService(config)
    assert isinstance(replica_b.events, RedisEventBus)
    assert replica_b.events.listening.wait(5)
    payload = load_fixture("incident_complete.json")
    try:

        async def scenario() -> dict[str, Any]:
            async with aclosing(replica_b.events.subscribe()) as subscription:
                queue = await anext(subscription)
                started = threading.Thread(target=replica_a.start, args=(payload,))
                started.start()
                event = await asyncio.wait_for(queue.get(), 10)
                started.join()
                return event

        event = asyncio.run(scenario())
    finally:
        replica_a.close()
        replica_b.close()

    assert event["type"] == "incident" and event["run_id"] == payload["run_id"]
