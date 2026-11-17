export type RunStatus =
  | "running"
  | "busy"
  | "awaiting_approval"
  | "needs_human"
  | "succeeded"
  | "failed"
  | "cancelled";

export type Severity = "SEV-1" | "SEV-2" | "SEV-3";

export interface ActionItem {
  type: string;
  description: string;
  evidence: string[];
}

export interface EvidenceBlock {
  source: "monitoring" | "github" | "runbooks" | string;
  data: unknown;
}

export interface RunbookHit {
  path: string;
  title: string;
  excerpt: string;
}

export interface AiInfo {
  provider: string;
  fallback_reason: string | null;
  input_tokens: number;
  output_tokens: number;
}

export interface Incident {
  run_id: string;
  idempotency_key: string;
  status: RunStatus;
  severity: Severity | null;
  confidence: number | null;
  summary: string;
  service: string;
  owner: string;
  reporter: string;
  symptoms: string[];
  missing_fields: string[];
  conflicts: string[];
  hypotheses: string[];
  proposed_actions: string[];
  approved_actions: string[];
  action_items: ActionItem[];
  evidence: EvidenceBlock[];
  runbook_hits: RunbookHit[];
  timeline: string[];
  retries: number;
  approval: "approved" | "rejected" | null;
  action_dispatch: "sent" | "not_configured" | "failed" | null;
  ai: AiInfo | null;
  ai_review: { provider: string; verdict: string; fallback_reason: string | null } | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface AuditEntry {
  event: string;
  actor: string;
  detail: Record<string, unknown>;
  at: string;
}

export interface NewIncident {
  channel_id: string;
  thread_ts: string;
  reporter: string;
  summary: string;
  run_id?: string;
  service?: string;
  owner?: string;
  symptoms?: string[];
  monitoring?: Record<string, unknown>;
  github?: Record<string, unknown>;
}

export interface Decision {
  decision: "approved" | "rejected";
  approved_actions?: string[];
}
