import { pipelineStages } from "./pipeline";

const states = (status: Parameters<typeof pipelineStages>[0]) =>
  Object.fromEntries(pipelineStages(status).map((s) => [s.id, s.state]));

describe("pipelineStages", () => {
  it("shows the commander's decision as the current step while waiting", () => {
    expect(states({ status: "awaiting_approval", approval: null, action_dispatch: null })).toMatchObject({
      review: "done",
      approval: "current",
      handoff: "pending",
    });
  });

  it("marks the review step as escalated when a person must take over", () => {
    expect(states({ status: "needs_human", approval: null, action_dispatch: null })).toMatchObject({
      review: "warning",
      approval: "pending",
    });
  });

  it("completes every step after an approved hand-off", () => {
    const result = states({ status: "succeeded", approval: "approved", action_dispatch: "sent" });
    expect(Object.values(result).every((s) => s === "done")).toBe(true);
  });

  it("is honest when n8n is not configured", () => {
    const stages = pipelineStages({ status: "succeeded", approval: "approved", action_dispatch: "not_configured" });
    expect(stages.at(-1)).toMatchObject({ state: "skipped", note: "n8n not configured" });
  });

  it("shows a failed hand-off and a rejection distinctly", () => {
    expect(states({ status: "failed", approval: "approved", action_dispatch: "failed" }).handoff).toBe("error");
    expect(states({ status: "cancelled", approval: "rejected", action_dispatch: null })).toMatchObject({
      approval: "done",
      handoff: "skipped",
    });
  });

  it("shows a queued run as waiting for a worker, before any analysis step", () => {
    const stages = pipelineStages({ status: "queued", approval: null, action_dispatch: null });
    expect(Object.fromEntries(stages.map((s) => [s.id, s.state]))).toMatchObject({
      intake: "done",
      research: "pending",
      triage: "pending",
      approval: "pending",
    });
    expect(stages.find((s) => s.id === "research")?.note).toBe("Waiting for a worker");
  });

  it("marks the analysis step as failed when the run errored", () => {
    expect(states({ status: "errored", approval: null, action_dispatch: null })).toMatchObject({
      intake: "done",
      research: "error",
      approval: "pending",
    });
  });

  it("shows work in progress at triage", () => {
    expect(states({ status: "running", approval: null, action_dispatch: null })).toMatchObject({
      triage: "current",
      review: "pending",
    });
  });
});
