import { Loader2, Sparkles } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useCreateIncident } from "../hooks/useIncidents";
import { useToast } from "../hooks/useToast";
import { SAMPLES } from "../lib/samples";

export function SamplePicker() {
  const create = useCreateIncident();
  const navigate = useNavigate();
  const toast = useToast();
  const [active, setActive] = useState<string | null>(null);

  const run = (id: string) => {
    const sample = SAMPLES.find((s) => s.id === id);
    if (!sample) return;
    setActive(id);
    create.mutate(sample.build(), {
      onSuccess: (incident) => navigate(`/incidents/${encodeURIComponent(incident.run_id)}`),
      onError: (error) => toast("error", error.message),
      onSettled: () => setActive(null),
    });
  };

  return (
    <ul className="grid gap-3 sm:grid-cols-3">
      {SAMPLES.map((sample) => (
        <li key={sample.id}>
          <button
            type="button"
            onClick={() => run(sample.id)}
            disabled={create.isPending}
            className="bg-surface hover:border-brand flex h-full w-full flex-col items-start gap-2 rounded-xl border border-line p-4 text-left disabled:opacity-60"
          >
            <span className="text-brand flex items-center gap-1.5 text-sm font-semibold">
              {active === sample.id ? (
                <Loader2 className="size-4 animate-spin" aria-hidden />
              ) : (
                <Sparkles className="size-4" aria-hidden />
              )}
              {active === sample.id ? "Analyzing…" : sample.title}
            </span>
            <span className="text-muted text-sm">{sample.blurb}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
