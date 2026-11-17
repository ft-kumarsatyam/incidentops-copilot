import type { Incident } from "./types";

export type StageState = "done" | "current" | "pending" | "warning" | "error" | "skipped";

export interface Stage {
  id: "intake" | "research" | "triage" | "review" | "approval" | "handoff";
  label: string;
  state: StageState;
  note?: string;
}

/** Derive a six-step progress view from the run status. Pure, so it is easy to test. */
export function pipelineStages(incident: Pick<Incident, "status" | "approval" | "action_dispatch">): Stage[] {
  const { status, approval, action_dispatch: dispatch } = incident;
  const stages: Stage[] = [
    { id: "intake", label: "Report received", state: "done" },
    { id: "research", label: "Evidence gathered", state: "done" },
    { id: "triage", label: "Triage", state: "done" },
    { id: "review", label: "Safety review", state: "done" },
    { id: "approval", label: "Your decision", state: "pending" },
    { id: "handoff", label: "Hand-off", state: "pending" },
  ];
  const set = (id: Stage["id"], state: StageState, note?: string) => {
    const stage = stages.find((s) => s.id === id);
    if (stage) {
      stage.state = state;
      if (note) stage.note = note;
    }
  };

  switch (status) {
    case "running":
    case "busy":
      set("triage", "current");
      set("review", "pending");
      break;
    case "awaiting_approval":
      set("approval", "current");
      break;
    case "needs_human":
      set("review", "warning", "Escalated");
      set("approval", "pending", "A person must take over");
      break;
    case "cancelled":
      set("approval", "done", "Rejected");
      set("handoff", "skipped", "Nothing sent");
      break;
    case "succeeded":
      set("approval", "done", approval === "approved" ? "Approved" : undefined);
      if (dispatch === "not_configured") set("handoff", "skipped", "n8n not configured");
      else set("handoff", "done", "Sent to n8n");
      break;
    case "failed":
      set("approval", "done", "Approved");
      set("handoff", "error", "Could not send");
      break;
  }
  return stages;
}
