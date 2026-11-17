import { AlertTriangle, Check, CircleDashed, Loader2, MinusCircle, X } from "lucide-react";
import { cn } from "../lib/cn";
import { pipelineStages, type Stage, type StageState } from "../lib/pipeline";
import type { Incident } from "../lib/types";

const ICON: Record<StageState, typeof Check> = {
  done: Check,
  current: Loader2,
  pending: CircleDashed,
  warning: AlertTriangle,
  error: X,
  skipped: MinusCircle,
};

const STYLE: Record<StageState, string> = {
  done: "bg-okbg text-ok border-ok/30",
  current: "bg-warnbg text-warn border-warn/40",
  pending: "bg-surface2 text-muted border-line",
  warning: "bg-dangerbg text-danger border-danger/30",
  error: "bg-dangerbg text-danger border-danger/30",
  skipped: "bg-neutralbg text-muted border-line",
};

const SR: Record<StageState, string> = {
  done: "completed",
  current: "in progress",
  pending: "not started",
  warning: "needs attention",
  error: "failed",
  skipped: "skipped",
};

function Step({ stage }: { stage: Stage }) {
  const Icon = ICON[stage.state];
  return (
    <li className="flex min-w-0 flex-1 items-start gap-2 sm:flex-col sm:items-center sm:text-center">
      <span
        className={cn("grid size-8 shrink-0 place-items-center rounded-full border", STYLE[stage.state])}
        aria-hidden
      >
        <Icon className={cn("size-4", stage.state === "current" && "animate-spin")} />
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-medium">
          {stage.label}
          <span className="sr-only"> ({SR[stage.state]})</span>
        </span>
        {stage.note && <span className="text-muted block text-xs">{stage.note}</span>}
      </span>
    </li>
  );
}

export function Pipeline({ incident }: { incident: Incident }) {
  const stages = pipelineStages(incident);
  return (
    <ol aria-label="Progress" className="flex flex-col gap-3 sm:flex-row sm:gap-2">
      {stages.map((stage) => (
        <Step key={stage.id} stage={stage} />
      ))}
    </ol>
  );
}
