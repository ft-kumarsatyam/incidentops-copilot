import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  Clock,
  Flame,
  Info,
  Loader2,
  UserRound,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { cn } from "../lib/cn";
import { SEVERITY, providerLabel, statusMeta, type Tone } from "../lib/format";
import type { Severity } from "../lib/types";

export const toneClasses: Record<Tone, string> = {
  neutral: "bg-neutralbg text-muted",
  info: "bg-infobg text-info",
  warn: "bg-warnbg text-warn",
  danger: "bg-dangerbg text-danger",
  ok: "bg-okbg text-ok",
};

const STATUS_ICON: Record<string, LucideIcon> = {
  running: Loader2,
  busy: Loader2,
  awaiting_approval: Clock,
  needs_human: UserRound,
  succeeded: CheckCircle2,
  cancelled: Ban,
  failed: XCircle,
};

const SEVERITY_ICON: Record<Severity, LucideIcon> = {
  "SEV-1": Flame,
  "SEV-2": AlertTriangle,
  "SEV-3": Info,
};

const pill = "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold whitespace-nowrap";

export function StatusBadge({ status }: { status: string }) {
  const meta = statusMeta(status);
  const Icon = STATUS_ICON[status] ?? Info;
  const spinning = status === "running" || status === "busy";
  return (
    <span className={cn(pill, toneClasses[meta.tone])} title={meta.hint}>
      <Icon className={cn("size-3.5", spinning && "animate-spin")} aria-hidden />
      {meta.label}
    </span>
  );
}

export function SeverityBadge({ severity }: { severity: Severity | null }) {
  if (!severity) {
    return <span className={cn(pill, toneClasses.neutral)}>Unrated</span>;
  }
  const meta = SEVERITY[severity];
  const Icon = SEVERITY_ICON[severity];
  return (
    <span className={cn(pill, toneClasses[meta.tone])}>
      <Icon className="size-3.5" aria-hidden />
      {severity} · {meta.label}
    </span>
  );
}

export function ConfidenceMeter({ value }: { value: number | null }) {
  if (value === null) return <span className="text-muted text-sm">n/a</span>;
  const pct = Math.round(value * 100);
  const tone = pct >= 80 ? "bg-ok" : pct >= 50 ? "bg-warn" : "bg-danger";
  return (
    <div className="flex items-center gap-2">
      <div
        role="meter"
        aria-label="Confidence"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        className="bg-neutralbg h-2 w-24 overflow-hidden rounded-full"
      >
        <div className={cn("h-full rounded-full", tone)} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-sm font-medium tabular-nums">{pct}%</span>
    </div>
  );
}

export function ProviderBadge({ provider, fallbackReason }: { provider: string; fallbackReason?: string | null }) {
  return (
    <span
      className={cn(pill, "bg-neutralbg text-muted font-medium")}
      title={fallbackReason ? `Fell back to rules: ${fallbackReason}` : undefined}
    >
      {providerLabel(provider)}
      {fallbackReason ? " · fallback" : ""}
    </span>
  );
}
