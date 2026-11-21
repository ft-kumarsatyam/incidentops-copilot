# ADR 004: Server-Sent Events for live updates

**Status:** Accepted
**Date:** 2026-11-17

## Context

Two people can look at the same incident, and a decision made in one browser should
appear in the other without a refresh. Updates flow only from server to client.

## Decision

Expose `GET /incidents/stream` as Server-Sent Events. The service publishes
`{type, run_id, status}` to an in-process `EventBus`, and clients refetch what they show.
The UI reads the stream with `fetch` rather than `EventSource` so it can send
`X-API-Key`. It reconnects on drop and falls back to polling every 15 seconds.

## Alternatives

- **WebSocket:** two-way, which we do not need. More moving parts through proxies.
- **Polling only:** simple, but slow to reflect a decision and wasteful at scale.
- **`EventSource`:** cannot send custom headers, so it conflicts with header auth.

## Consequences

- Plain HTTP, works through most proxies. Disable proxy buffering (`X-Accel-Buffering: no` is set).
- The default bus is in-process, so with several API replicas a client only sees events
  from the replica it is connected to. Set `REDIS_URL` to use the Redis pub/sub bus
  (`api/events.py`); clients also fall back to polling. Pub/sub does not replay missed
  events; Redis Streams with `Last-Event-ID` is on the roadmap
  ([ADR 012](012-target-architecture-roadmap.md)).
