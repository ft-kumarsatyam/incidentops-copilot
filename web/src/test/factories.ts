import type { Incident } from "../lib/types";

export function incident(overrides: Partial<Incident> = {}): Incident {
  return {
    run_id: "inc-1",
    idempotency_key: "inc-1",
    status: "awaiting_approval",
    severity: "SEV-1",
    confidence: 0.9,
    summary: "Checkout is failing",
    service: "checkout-api",
    owner: "payments-oncall",
    reporter: "U1",
    symptoms: [],
    missing_fields: [],
    conflicts: [],
    hypotheses: [],
    proposed_actions: ["Create incident record", "Notify the channel", "Roll back abc123"],
    approved_actions: [],
    action_items: [
      { type: "open_issue", description: "Create incident record", evidence: ["report"] },
      { type: "notify", description: "Notify the channel", evidence: ["report"] },
      { type: "rollback", description: "Roll back abc123", evidence: ["github"] },
    ],
    evidence: [],
    runbook_hits: [],
    timeline: ["incident received"],
    retries: 1,
    approval: null,
    action_dispatch: null,
    ai: null,
    ai_review: null,
    created_at: "2026-01-01T00:00:00.000Z",
    updated_at: "2026-01-01T00:00:00.000Z",
    ...overrides,
  };
}
