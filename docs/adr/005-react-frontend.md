# ADR 005: React, Vite and TypeScript for the UI

**Status:** Accepted
**Date:** 2026-11-17

## Context

The first dashboard was one page of vanilla JavaScript that polled every 15 seconds and
could not create an incident. The people using it are incident commanders, often under
stress and not always engineers, so clarity matters more than density.

## Decision

Build `web/` with React, Vite and TypeScript, styled with Tailwind design tokens
(light, dark, system), data through TanStack Query, routing through `react-router-dom`
using `HashRouter` so UI routes never collide with API paths. Vite builds into
`src/incidentops/static`, which FastAPI serves at `/`, and the Python wheel ships it.
Docker builds the UI in a Node stage first.

Design rules:

- Meaning never relies on colour alone (icons and text accompany severity and status).
- A guided first run with a sample incident, so the app is useful in two clicks.
- Approval always goes through a confirm dialog that states what will be sent.
- Keyboard first, with a skip link and visible focus, and a layout that works on a phone.

## Alternatives

- **Keep the vanilla page:** no typing, no tests, hard to grow.
- **Next.js:** server rendering adds a second server we do not need.
- **HTMX:** attractive for a small UI, but the live pipeline and checklist state are easier to test in React.

## Consequences

- Node is needed to build the UI, but not to run the API (a hint page appears if the UI is not built).
- One more toolchain to keep updated. CI builds and tests it, and Dependabot tracks it.
- The UI has 48 tests and a typecheck gate.
