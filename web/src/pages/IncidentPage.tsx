import { ArrowLeft, Loader2, RotateCcw } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { ActionChecklist } from "../components/ActionChecklist";
import { ApprovalPanel } from "../components/ApprovalPanel";
import { ConfidenceMeter, ProviderBadge, SeverityBadge, StatusBadge } from "../components/Badges";
import { EvidencePanel } from "../components/EvidencePanel";
import { Pipeline } from "../components/Pipeline";
import { QueryError } from "../components/QueryError";
import { Skeleton } from "../components/States";
import { useAudit, useCan, useDecide, useIncident, useRetryDispatch, useRetryRun } from "../hooks/useIncidents";
import { useToast } from "../hooks/useToast";
import { humanizeTimeline, relativeTime } from "../lib/format";
import type { AuditEntry, Decision, Incident } from "../lib/types";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold">{title}</h2>
      {children}
    </section>
  );
}

const EVENT_LABEL: Record<string, string> = {
  created: "Incident created",
  "decision:approved": "Approved",
  "decision:rejected": "Rejected",
  "dispatch:sent": "Actions sent to n8n",
  "dispatch:failed": "Hand-off to n8n failed",
  "dispatch:not_configured": "n8n not configured, nothing sent",
};

function Audit({ entries }: { entries: AuditEntry[] }) {
  return (
    <ol className="space-y-2 text-sm">
      {entries.map((entry, index) => (
        <li key={index} className="flex flex-wrap gap-x-3">
          <span className="font-medium">{EVENT_LABEL[entry.event] ?? entry.event}</span>
          <span className="text-muted">
            by {entry.actor} · {relativeTime(entry.at)}
          </span>
        </li>
      ))}
    </ol>
  );
}

function WhyHuman({ incident }: { incident: Incident }) {
  const critic = [...incident.timeline].reverse().find((line) => line.startsWith("critic:"));
  return (
    <div role="note" className="bg-dangerbg text-danger rounded-xl p-4">
      <p className="font-semibold">A person needs to take over</p>
      <p className="mt-1 text-sm">The agents stopped instead of guessing. Here is why:</p>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
        {incident.missing_fields.length > 0 && <li>Missing information: {incident.missing_fields.join(", ")}.</li>}
        {incident.conflicts.map((conflict) => (
          <li key={conflict}>{conflict}.</li>
        ))}
        {critic && <li>{critic.replace("critic: ", "Review result: ")}</li>}
      </ul>
    </div>
  );
}

export function IncidentPage() {
  const { runId = "" } = useParams();
  const { data: incident, isPending, error, refetch } = useIncident(runId);
  const { data: audit } = useAudit(runId);
  const toast = useToast();
  const decide = useDecide(incident ?? ({ run_id: runId, idempotency_key: "" } as Incident));
  const retry = useRetryDispatch(runId);
  const retryRun = useRetryRun(runId);
  const can = useCan();

  const onDecide = async (decision: Decision) => {
    try {
      const updated = await decide.mutateAsync(decision);
      toast(
        "success",
        decision.decision === "approved"
          ? updated.status === "failed"
            ? "Approved, but the hand-off failed. You can retry it."
            : "Approved. The actions were handed off."
          : "Rejected. No actions were taken.",
      );
    } catch (failure) {
      toast("error", failure instanceof Error ? failure.message : "Could not save your decision.");
    }
  };

  if (error) return <QueryError error={error} onRetry={() => void refetch()} />;
  if (isPending) {
    return (
      <div className="space-y-4" aria-busy="true" aria-label="Loading incident">
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-24" />
        <Skeleton className="h-48" />
      </div>
    );
  }

  return (
    <article className="space-y-8">
      <div className="space-y-3">
        <Link to="/" className="text-muted hover:text-fg inline-flex items-center gap-1 text-sm">
          <ArrowLeft className="size-4" aria-hidden /> All incidents
        </Link>
        <div className="flex flex-wrap items-center gap-2">
          <SeverityBadge severity={incident.severity} />
          <StatusBadge status={incident.status} />
        </div>
        <h1 className="text-2xl font-semibold">{incident.summary}</h1>
        <p className="text-muted flex flex-wrap gap-x-4 gap-y-1 text-sm">
          {incident.service && <span>Service: {incident.service}</span>}
          {incident.owner && <span>Owner: {incident.owner}</span>}
          <span>Reported by {incident.reporter}</span>
          <span>{relativeTime(incident.created_at)}</span>
          <span className="font-mono text-xs">{incident.run_id}</span>
        </p>
      </div>

      <div className="bg-surface rounded-xl border border-line p-5">
        <Pipeline incident={incident} />
      </div>

      {incident.status === "awaiting_approval" && (
        <ApprovalPanel
          key={incident.run_id}
          incident={incident}
          busy={decide.isPending}
          onDecide={onDecide}
          canDecide={can("incident:decide")}
        />
      )}
      {incident.status === "needs_human" && <WhyHuman incident={incident} />}
      {incident.status === "errored" && (
        <div role="alert" className="bg-dangerbg text-danger rounded-xl p-4">
          <p className="font-semibold">The analysis could not be completed</p>
          <p className="mt-1 text-sm">
            It failed after several tries{incident.error ? `: ${incident.error}` : "."} Nothing was changed.
          </p>
          {can("incident:retry") && (
            <button
              type="button"
              disabled={retryRun.isPending}
              onClick={() =>
                retryRun.mutate(undefined, {
                  onSuccess: () => toast("success", "Analysis queued again."),
                  onError: (failure) => toast("error", failure.message),
                })
              }
              className="mt-3 inline-flex items-center gap-2 rounded-lg border border-current px-3 py-1.5 text-sm font-semibold disabled:opacity-60"
            >
              {retryRun.isPending ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <RotateCcw className="size-4" aria-hidden />}
              Retry analysis
            </button>
          )}
        </div>
      )}
      {incident.status === "failed" && (
        <div role="alert" className="bg-dangerbg text-danger rounded-xl p-4">
          <p className="font-semibold">Approved, but the actions could not be sent</p>
          <p className="mt-1 text-sm">The automation service did not respond. Nothing was changed.</p>
          {can("incident:retry") && (
            <button
              type="button"
              disabled={retry.isPending}
              onClick={() =>
                retry.mutate(undefined, {
                  onSuccess: () => toast("success", "Actions handed off."),
                  onError: (failure) => toast("error", failure.message),
                })
              }
              className="mt-3 inline-flex items-center gap-2 rounded-lg border border-current px-3 py-1.5 text-sm font-semibold disabled:opacity-60"
            >
              {retry.isPending ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <RotateCcw className="size-4" aria-hidden />}
              Retry hand-off
            </button>
          )}
        </div>
      )}

      <div className="grid gap-8 lg:grid-cols-[2fr_1fr]">
        <div className="space-y-8">
          <Section title="Assessment">
            <div className="bg-surface flex flex-wrap items-center gap-x-8 gap-y-3 rounded-xl border border-line p-4">
              <div>
                <p className="text-muted text-xs">Confidence</p>
                <ConfidenceMeter value={incident.confidence} />
              </div>
              {incident.ai && (
                <div>
                  <p className="text-muted text-xs">Assessed by</p>
                  <ProviderBadge provider={incident.ai.provider} fallbackReason={incident.ai.fallback_reason} />
                </div>
              )}
            </div>
            {incident.hypotheses.length > 0 && (
              <div>
                <h3 className="mb-1 text-sm font-semibold">Likely causes</h3>
                <ul className="list-disc space-y-1 pl-5 text-sm">
                  {incident.hypotheses.map((hypothesis) => (
                    <li key={hypothesis}>{hypothesis}</li>
                  ))}
                </ul>
              </div>
            )}
          </Section>

          {incident.status !== "awaiting_approval" && incident.proposed_actions.length > 0 && (
            <Section title="Proposed actions">
              <ActionChecklist incident={incident} />
            </Section>
          )}

          <Section title="Evidence">
            <EvidencePanel incident={incident} />
          </Section>
        </div>

        <div className="space-y-8">
          <Section title="What happened">
            <ol className="border-line space-y-3 border-l pl-4 text-sm">
              {incident.timeline.map((line, index) => (
                <li key={index} className="relative">
                  <span className="bg-line absolute top-1.5 -left-[1.3rem] size-2 rounded-full" aria-hidden />
                  {humanizeTimeline(line)}
                </li>
              ))}
            </ol>
          </Section>
          {audit && audit.length > 0 && (
            <Section title="Activity log">
              <Audit entries={audit} />
            </Section>
          )}
          <details className="text-sm">
            <summary className="text-muted cursor-pointer">Technical details</summary>
            <pre className="bg-surface2 mt-2 max-h-96 overflow-auto rounded-lg p-3 text-xs">
              {JSON.stringify(incident, null, 2)}
            </pre>
          </details>
        </div>
      </div>
    </article>
  );
}
