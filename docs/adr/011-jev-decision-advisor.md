# ADR 011: Jev as a decision advisor, behind RBAC

**Status:** Accepted  
**Date:** 2026-11-21

## Context

TypeSafe's Jev is a hosted "System One" model: it answers typed questions (yes/no, pick one,
score) with probabilities, quickly and cheaply, but cannot write text. That suits narrow
decisions beside the LLM agents. It is closed, hosted and new, with vendor-reported benchmarks
and no independent evaluation we know of, so it must not be able to approve or waive anything.

## Decision

- **Advisor only.** In `assess`, Jev gives a severity opinion and a prompt-injection score. A
  disagreement at or above `JEV_MIN_CONFIDENCE` adds a conflict (the critic then retries and
  escalates to a person); an injection score at or above `JEV_INJECTION_THRESHOLD` adds the
  `jev:injection` guardrail flag. It never changes the severity and never removes a conflict.
  The conflict is re-derived on every research pass, so retrying cannot make it disappear. The
  advice is stored on the run (`jev`) and reused across passes.
- **Fail open to the existing behaviour.** Any Jev error means no advice and a timeline note. A
  circuit breaker (see `BREAKER_*`) stops calling Jev after repeated 5xx, 429 or timeouts, so an
  outage costs one fast refusal per incident instead of a timeout.
- **Data minimisation.** Only the summary, service, symptoms, monitoring figures and a
  recent-deploy boolean are sent, after redaction. Owner and reporter are not.
- **RBAC.** `decision:invoke` (responder) for `POST /decisions`; `decision:admin` (admin) for
  `GET /decisions/status`. `JEV_ALLOWED_TENANTS` narrows the first. The model is fixed by
  configuration, so a caller cannot pick a costlier one. Only `noul`, `choice` and `score`
  questions are accepted (no images). Audit entries (`decision:invoked`, `decision:failed`,
  `decision:forbidden`) record the actor, question names and token counts, not the content.
- **Off by default.** `JEV_ENABLED` needs `JEV_API_KEY`; the service refuses to start without it.
- **Observable.** Calls, errors, tokens and breaker state are on `/metrics`.

## Consequences

- An extra outbound call per incident (once, not per retry) with a short timeout.
- Jev's judgement is unverified here. Run it in advisory mode and compare against `make eval`
  before relying on it for anything more.
- The API's rate limits and its 401/403/429/5xx behaviour are undocumented; every non-200 answer
  is treated as a failure, and only 5xx, 429 and transport errors count towards the breaker.
