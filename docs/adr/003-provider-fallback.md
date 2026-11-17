# ADR 003: Claude behind a provider interface, with a rules fallback

**Status:** Accepted
**Date:** 2026-11-17

## Context

An LLM improves triage on messy reports, but a tool that stops working when a key is
missing, a quota is hit, or the model answers badly is a poor incident tool. It should
also be possible to clone the repo and see it work with nothing configured.

## Decision

Define a `TriageProvider` protocol with three implementations:

- `RulesProvider`: deterministic, offline, complete.
- `ClaudeProvider`: structured output validated by Pydantic models.
- `ResilientProvider`: tries the primary, and on any error, timeout, or invalid output
  uses the rules provider and records the reason on the incident.

`AI_PROVIDER=auto` picks Claude when `ANTHROPIC_API_KEY` is set. Model output then
passes the same code-level guardrails as the rules output (closed action list,
evidence grounding, severity versus measurements, bounds). The critic model can add
concerns but cannot remove them.

## Consequences

- The offline path is the test and CI baseline, and it costs nothing.
- Claude and rules can be scored on the same labeled incidents (`make eval`,
  `make eval-claude`).
- A fallback is visible, not silent: the UI shows the provider and the reason.
- Claude's accuracy is only as proven as the eval set. Ten cases are a smoke test, not a benchmark.

See [AI design](../AI_DESIGN.md).
