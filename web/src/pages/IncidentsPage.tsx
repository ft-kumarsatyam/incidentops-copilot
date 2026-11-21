import { ChevronRight, Plus, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ConfidenceMeter, SeverityBadge, StatusBadge } from "../components/Badges";
import { QueryError } from "../components/QueryError";
import { SamplePicker } from "../components/SamplePicker";
import { EmptyState, Skeleton } from "../components/States";
import { useCan, useIncidents } from "../hooks/useIncidents";
import { cn } from "../lib/cn";
import { inTab, matchesQuery, relativeTime, sortIncidents, statusMeta, type Tab } from "../lib/format";
import type { Incident } from "../lib/types";

const TABS: { id: Tab; label: string }[] = [
  { id: "open", label: "Open" },
  { id: "resolved", label: "Resolved" },
  { id: "all", label: "All" },
];

function Row({ incident }: { incident: Incident }) {
  const attention = statusMeta(incident.status).needsAttention;
  return (
    <li>
      <Link
        to={`/incidents/${encodeURIComponent(incident.run_id)}`}
        className={cn(
          "bg-surface hover:border-brand flex items-center gap-3 rounded-xl border p-4",
          attention ? "border-warn/50" : "border-line",
        )}
      >
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <SeverityBadge severity={incident.severity} />
            <StatusBadge status={incident.status} />
          </div>
          <p className="truncate font-medium">{incident.summary || incident.run_id}</p>
          <p className="text-muted flex flex-wrap gap-x-3 text-sm">
            {incident.service && <span>{incident.service}</span>}
            {incident.owner && <span>Owner: {incident.owner}</span>}
            <span>{relativeTime(incident.updated_at)}</span>
          </p>
        </div>
        <div className="hidden sm:block">
          <ConfidenceMeter value={incident.confidence} />
        </div>
        <ChevronRight className="text-muted size-5 shrink-0" aria-hidden />
      </Link>
    </li>
  );
}

export function IncidentsPage() {
  const { data, isPending, error, refetch } = useIncidents();
  const canReport = useCan()("incident:create");
  const [tab, setTab] = useState<Tab>("open");
  const [query, setQuery] = useState("");

  const counts = useMemo(
    () => ({
      open: data?.filter((i) => inTab(i, "open")).length ?? 0,
      resolved: data?.filter((i) => inTab(i, "resolved")).length ?? 0,
      all: data?.length ?? 0,
    }),
    [data],
  );
  const attention = data?.filter((i) => statusMeta(i.status).needsAttention).length ?? 0;
  const visible = useMemo(
    () => sortIncidents((data ?? []).filter((i) => inTab(i, tab) && matchesQuery(i, query))),
    [data, tab, query],
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Incidents</h1>
          <p className="text-muted text-sm" aria-live="polite">
            {data
              ? attention > 0
                ? `${attention} ${attention === 1 ? "incident needs" : "incidents need"} your attention.`
                : "Nothing is waiting on you."
              : "Loading…"}
          </p>
        </div>
        {canReport && (
          <Link
            to="/new"
            className="bg-brand text-brandfg hover:bg-brandhover inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold"
          >
            <Plus className="size-4" aria-hidden /> Report an incident
          </Link>
        )}
      </div>

      {error ? (
        <QueryError error={error} onRetry={() => void refetch()} />
      ) : isPending ? (
        <div className="space-y-3" aria-busy="true" aria-label="Loading incidents">
          {[0, 1, 2].map((n) => (
            <Skeleton key={n} className="h-24" />
          ))}
        </div>
      ) : data.length === 0 ? (
        <div className="space-y-6">
          <EmptyState title="No incidents yet">
            {canReport
              ? "Report one, or try a sample to see how the agents investigate and propose a response."
              : "Incidents will appear here when someone reports one."}
          </EmptyState>
          {canReport && <SamplePicker />}
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <div role="tablist" aria-label="Filter incidents" className="bg-neutralbg flex rounded-lg p-1">
              {TABS.map(({ id, label }) => (
                <button
                  key={id}
                  role="tab"
                  type="button"
                  aria-selected={tab === id}
                  onClick={() => setTab(id)}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-sm font-medium",
                    tab === id ? "bg-surface shadow-sm" : "text-muted hover:text-fg",
                  )}
                >
                  {label} <span className="text-muted tabular-nums">{counts[id]}</span>
                </button>
              ))}
            </div>
            <label className="relative min-w-48 flex-1">
              <span className="sr-only">Search incidents</span>
              <Search className="text-muted absolute top-1/2 left-3 size-4 -translate-y-1/2" aria-hidden />
              <input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search by service, summary or ID"
                className="bg-surface w-full rounded-lg border border-line py-2 pr-3 pl-9 text-sm"
              />
            </label>
          </div>
          {visible.length === 0 ? (
            <EmptyState title="No matching incidents">Try another tab or clear the search.</EmptyState>
          ) : (
            <ul className="space-y-3" role="tabpanel">
              {visible.map((incident) => (
                <Row key={incident.run_id} incident={incident} />
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
