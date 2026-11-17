import { Loader2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { SamplePicker } from "../components/SamplePicker";
import { useCreateIncident } from "../hooks/useIncidents";
import { useSettings } from "../lib/settings";
import type { NewIncident } from "../lib/types";

const input = "bg-surface w-full rounded-lg border border-line px-3 py-2 text-sm";

export function buildIncident(form: FormData, reporter: string, now = Date.now()): NewIncident {
  const text = (name: string) => String(form.get(name) ?? "").trim();
  const service = text("service");
  const incident: NewIncident = {
    channel_id: "C-WEB",
    thread_ts: (now / 1000).toFixed(4),
    reporter: reporter || "web-user",
    summary: text("summary"),
  };
  if (service) incident.service = service;
  if (text("owner")) incident.owner = text("owner");
  const symptoms = text("symptoms")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (symptoms.length) incident.symptoms = symptoms;
  const rate = Number(text("error_rate"));
  if (text("error_rate") && Number.isFinite(rate) && rate >= 0) {
    incident.monitoring = { ...(service ? { service } : {}), error_rate: Math.min(rate, 100) / 100 };
  }
  if (text("repo") || text("deploy")) {
    incident.github = {
      ...(text("repo") ? { repo: text("repo") } : {}),
      ...(text("deploy") ? { recent_deploy: text("deploy") } : {}),
    };
  }
  return incident;
}

export function NewIncidentPage() {
  const create = useCreateIncident();
  const navigate = useNavigate();
  const { actor } = useSettings();
  const [error, setError] = useState<string | null>(null);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    create.mutate(buildIncident(new FormData(event.currentTarget), actor), {
      onSuccess: (incident) => navigate(`/incidents/${encodeURIComponent(incident.run_id)}`),
      onError: (failure) => setError(failure.message),
    });
  };

  return (
    <div className="mx-auto max-w-2xl space-y-8">
      <div>
        <h1 className="text-2xl font-semibold">Report an incident</h1>
        <p className="text-muted mt-1 text-sm">
          Describe what you see. The agents will gather evidence and propose a response for you to
          approve. Nothing is done without your say-so.
        </p>
      </div>

      <form onSubmit={submit} className="space-y-5">
        <div>
          <label htmlFor="summary" className="mb-1 block text-sm font-medium">
            What is happening? <span className="text-danger">*</span>
          </label>
          <textarea
            id="summary"
            name="summary"
            required
            maxLength={2000}
            rows={3}
            className={input}
            placeholder="e.g. Customers can't complete checkout; we see lots of 500 errors"
          />
        </div>

        <div className="grid gap-5 sm:grid-cols-2">
          <div>
            <label htmlFor="service" className="mb-1 block text-sm font-medium">
              Affected service
            </label>
            <input id="service" name="service" className={input} placeholder="checkout-api" />
          </div>
          <div>
            <label htmlFor="owner" className="mb-1 block text-sm font-medium">
              Owning team or person
            </label>
            <input id="owner" name="owner" className={input} placeholder="payments-oncall" />
          </div>
        </div>

        <div>
          <label htmlFor="symptoms" className="mb-1 block text-sm font-medium">
            Symptoms <span className="text-muted font-normal">(one per line)</span>
          </label>
          <textarea id="symptoms" name="symptoms" rows={3} className={input} placeholder={"12% error rate\nCheckout failures"} />
        </div>

        <details className="rounded-lg border border-line p-4">
          <summary className="cursor-pointer text-sm font-medium">More context (optional)</summary>
          <div className="mt-4 grid gap-5 sm:grid-cols-3">
            <div>
              <label htmlFor="error_rate" className="mb-1 block text-sm font-medium">
                Error rate (%)
              </label>
              <input id="error_rate" name="error_rate" type="number" min="0" max="100" step="0.1" className={input} />
            </div>
            <div>
              <label htmlFor="repo" className="mb-1 block text-sm font-medium">
                GitHub repo
              </label>
              <input id="repo" name="repo" className={input} placeholder="acme/checkout" />
            </div>
            <div>
              <label htmlFor="deploy" className="mb-1 block text-sm font-medium">
                Recent deploy
              </label>
              <input id="deploy" name="deploy" className={input} placeholder="abc123" />
            </div>
          </div>
        </details>

        {error && (
          <p role="alert" className="bg-dangerbg text-danger rounded-lg p-3 text-sm">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={create.isPending}
          className="bg-brand text-brandfg hover:bg-brandhover inline-flex items-center gap-2 rounded-lg px-5 py-2.5 text-sm font-semibold disabled:opacity-60"
        >
          {create.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
          {create.isPending ? "Agents are investigating…" : "Investigate"}
        </button>
      </form>

      <section aria-labelledby="try" className="space-y-3 border-t border-line pt-6">
        <h2 id="try" className="text-lg font-semibold">
          Or try a sample
        </h2>
        <SamplePicker />
      </section>
    </div>
  );
}
