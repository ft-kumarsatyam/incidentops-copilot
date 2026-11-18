import type { Incident, RunStatus, Severity } from "./types";

export type Tone = "neutral" | "info" | "warn" | "danger" | "ok";

export interface StatusMeta {
  label: string;
  tone: Tone;
  hint: string;
  needsAttention: boolean;
}

const STATUS: Record<RunStatus, StatusMeta> = {
  queued: { label: "Queued", tone: "neutral", hint: "Waiting for a worker to pick this up.", needsAttention: false },
  running: { label: "Analyzing", tone: "info", hint: "The agents are gathering evidence.", needsAttention: false },
  busy: { label: "Updating", tone: "info", hint: "A decision is being applied.", needsAttention: false },
  awaiting_approval: {
    label: "Needs approval",
    tone: "warn",
    hint: "Review the proposed actions and decide.",
    needsAttention: true,
  },
  needs_human: {
    label: "Needs a person",
    tone: "danger",
    hint: "The agents could not reach a safe conclusion.",
    needsAttention: true,
  },
  succeeded: { label: "Approved", tone: "ok", hint: "Approved actions were handed off.", needsAttention: false },
  cancelled: { label: "Rejected", tone: "neutral", hint: "No actions were taken.", needsAttention: false },
  errored: {
    label: "Analysis failed",
    tone: "danger",
    hint: "The analysis could not finish after several tries. Retry it.",
    needsAttention: true,
  },
  failed: {
    label: "Hand-off failed",
    tone: "danger",
    hint: "Approved, but the actions could not be sent.",
    needsAttention: true,
  },
};

export function statusMeta(status: string): StatusMeta {
  return STATUS[status as RunStatus] ?? { label: status, tone: "neutral", hint: "", needsAttention: false };
}

export const SEVERITY: Record<Severity, { label: string; tone: Tone }> = {
  "SEV-1": { label: "Critical", tone: "danger" },
  "SEV-2": { label: "Major", tone: "warn" },
  "SEV-3": { label: "Minor", tone: "info" },
};

export type Tab = "open" | "resolved" | "all";

export function inTab(incident: Incident, tab: Tab): boolean {
  if (tab === "all") return true;
  const resolved = incident.status === "succeeded" || incident.status === "cancelled";
  return tab === "resolved" ? resolved : !resolved;
}

export function matchesQuery(incident: Incident, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return [incident.summary, incident.service, incident.run_id, incident.owner].some((field) =>
    field.toLowerCase().includes(needle),
  );
}

/** Attention first, then newest. */
export function sortIncidents(items: Incident[]): Incident[] {
  return [...items].sort((a, b) => {
    const attention = Number(statusMeta(b.status).needsAttention) - Number(statusMeta(a.status).needsAttention);
    if (attention !== 0) return attention;
    return (b.updated_at ?? "").localeCompare(a.updated_at ?? "");
  });
}

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["day", 86_400],
  ["hour", 3_600],
  ["minute", 60],
];

export function relativeTime(iso: string | null, now = Date.now()): string {
  if (!iso) return "";
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000);
  const formatter = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return formatter.format(Math.round(seconds / size), unit);
  }
  return "just now";
}

export function percent(value: number | null): string {
  return value === null ? "n/a" : `${Math.round(value * 100)}%`;
}

export function providerLabel(provider: string): string {
  if (provider === "rules") return "Rules engine";
  if (provider.startsWith("claude:")) return `Claude (${provider.slice(7)})`;
  return provider;
}

export function evidenceLabel(ref: string): string {
  if (ref.startsWith("runbook:")) return `Runbook: ${ref.slice(8)}`;
  return { report: "Incident report", monitoring: "Monitoring", github: "GitHub" }[ref] ?? ref;
}

const CRITIC: Record<string, string> = {
  awaiting_approval: "Safety review passed",
  running: "Review asked for more evidence",
  needs_human: "Review escalated to a person",
};

/** Turn the engine's log lines into sentences a non-engineer can follow. */
export function humanizeTimeline(line: string): string {
  if (line === "incident received") return "Report received";
  if (line === "escalated to a human") return "Handed over to a person";
  let match = /^research attempt (\d+)$/.exec(line);
  if (match) return match[1] === "1" ? "Started gathering evidence" : `Gathered more evidence (attempt ${match[1]})`;
  match = /^retrieved (\d+) runbook chunks$/.exec(line);
  if (match) return `Found ${match[1]} relevant runbook ${match[1] === "1" ? "section" : "sections"}`;
  match = /^assessed as (SEV-\d) by (.+)$/.exec(line);
  if (match) return `Assessed as ${match[1]} by ${providerLabel(match[2]!)}`;
  match = /^critic: (\w+)(?: \((.*)\))?$/.exec(line);
  if (match) {
    const base = CRITIC[match[1]!] ?? `Review: ${match[1]}`;
    return match[2] ? `${base} (${match[2]})` : base;
  }
  match = /^human decision: (\w+)$/.exec(line);
  if (match) return match[1] === "approved" ? "A person approved" : "A person rejected";
  if (line === "actions dispatched to n8n") return "Actions handed off to n8n";
  if (line.startsWith("action dispatch failed")) return "Hand-off to n8n failed";
  if (line === "fetched latest commit from GitHub") return "Fetched the latest commit from GitHub";
  if (line.startsWith("github unavailable")) return "GitHub could not be reached";
  if (line === "monitoring timed out; impact is unverified") return "Monitoring timed out, so impact is unverified";
  return line;
}
