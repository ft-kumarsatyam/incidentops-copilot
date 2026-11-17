import { Activity, FileText, GitCommit } from "lucide-react";
import type { ReactNode } from "react";
import type { Incident, RunbookHit } from "../lib/types";

function Card({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <div className="bg-surface rounded-lg border border-line p-4">
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <span className="text-muted" aria-hidden>
          {icon}
        </span>
        {title}
      </h3>
      <div className="mt-3 text-sm">{children}</div>
    </div>
  );
}

function Pairs({ data }: { data: Record<string, unknown> }) {
  const NAMES: Record<string, string> = {
    error_rate: "Error rate",
    latency_ms: "Latency",
    recent_deploy: "Latest deploy",
    timed_out: "Monitoring timed out",
  };
  const label = (key: string) => NAMES[key] ?? key.replace(/_/g, " ");
  const show = (key: string, value: unknown) => {
    if (key === "error_rate" && typeof value === "number") return `${(value * 100).toFixed(1)}%`;
    if (key === "latency_ms" && typeof value === "number") return `${value} ms`;
    if (typeof value === "boolean") return value ? "yes" : "no";
    return String(value);
  };
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
      {Object.entries(data).map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="text-muted first-letter:uppercase">{label(key)}</dt>
          <dd className="font-medium break-words">{show(key, value)}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Render FTS highlights written as [term] without using dangerouslySetInnerHTML. */
function Excerpt({ text }: { text: string }) {
  const parts = text.split(/(\[[^\]]+\])/g);
  return (
    <p className="text-muted mt-1">
      {parts.map((part, index) =>
        part.startsWith("[") && part.endsWith("]") ? (
          <mark key={index} className="bg-warnbg text-fg rounded px-0.5">
            {part.slice(1, -1)}
          </mark>
        ) : (
          <span key={index}>{part}</span>
        ),
      )}
    </p>
  );
}

export function EvidencePanel({ incident }: { incident: Incident }) {
  const monitoring = incident.evidence.find((e) => e.source === "monitoring")?.data as Record<string, unknown> | undefined;
  const github = incident.evidence.find((e) => e.source === "github")?.data as Record<string, unknown> | undefined;
  const runbooks: RunbookHit[] = incident.runbook_hits;
  const empty = !monitoring && !github && runbooks.length === 0;

  return (
    <div className="space-y-3">
      {empty && <p className="text-muted text-sm">No supporting evidence was found for this incident.</p>}
      {monitoring && (
        <Card icon={<Activity className="size-4" />} title="Monitoring">
          <Pairs data={monitoring} />
        </Card>
      )}
      {github && (
        <Card icon={<GitCommit className="size-4" />} title="Recent change (GitHub)">
          <Pairs data={github} />
        </Card>
      )}
      {runbooks.length > 0 && (
        <Card icon={<FileText className="size-4" />} title="Runbook guidance">
          <ul className="space-y-3">
            {runbooks.map((hit) => (
              <li key={`${hit.path}-${hit.title}`}>
                <p className="font-medium">
                  {hit.title} <span className="text-muted font-normal">· {hit.path}</span>
                </p>
                <Excerpt text={hit.excerpt} />
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
