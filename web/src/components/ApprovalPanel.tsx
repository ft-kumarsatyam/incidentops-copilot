import { Check, X } from "lucide-react";
import { useState } from "react";
import type { Decision, Incident } from "../lib/types";
import { ActionChecklist } from "./ActionChecklist";
import { ConfirmDialog } from "./ConfirmDialog";

interface Props {
  incident: Incident;
  busy: boolean;
  onDecide: (decision: Decision) => Promise<unknown>;
}

export function ApprovalPanel({ incident, busy, onDecide }: Props) {
  const [selected, setSelected] = useState<Set<string>>(() => new Set(incident.proposed_actions));
  const [pending, setPending] = useState<"approved" | "rejected" | null>(null);

  const toggle = (description: string) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(description)) next.delete(description);
      else next.add(description);
      return next;
    });

  const chosen = incident.proposed_actions.filter((description) => selected.has(description));

  const confirm = async () => {
    if (!pending) return;
    try {
      await onDecide(
        pending === "approved" ? { decision: "approved", approved_actions: chosen } : { decision: "rejected" },
      );
    } finally {
      setPending(null);
    }
  };

  return (
    <section aria-labelledby="decide-title" className="rounded-xl border-2 border-warn/50 bg-surface p-5">
      <h2 id="decide-title" className="text-lg font-semibold">
        Your decision
      </h2>
      <p className="text-muted mt-1 text-sm">
        Untick anything you don't want done. Nothing happens until you confirm.
      </p>
      <div className="mt-4">
        <ActionChecklist incident={incident} selected={selected} onToggle={toggle} disabled={busy} />
      </div>
      <div className="mt-4 flex flex-wrap gap-3">
        <button
          type="button"
          disabled={busy || chosen.length === 0}
          onClick={() => setPending("approved")}
          className="bg-brand text-brandfg hover:bg-brandhover inline-flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold disabled:opacity-50"
        >
          <Check className="size-4" aria-hidden />
          Approve {chosen.length} {chosen.length === 1 ? "action" : "actions"}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => setPending("rejected")}
          className="hover:bg-surface2 inline-flex items-center gap-2 rounded-lg border border-line px-4 py-2.5 text-sm font-semibold disabled:opacity-50"
        >
          <X className="size-4" aria-hidden />
          Reject
        </button>
      </div>

      <ConfirmDialog
        open={pending !== null}
        busy={busy}
        title={pending === "approved" ? "Approve and hand off?" : "Reject this incident?"}
        confirmLabel={pending === "approved" ? "Yes, approve" : "Yes, reject"}
        tone={pending === "rejected" ? "danger" : "brand"}
        onConfirm={() => void confirm()}
        onCancel={() => !busy && setPending(null)}
      >
        {pending === "approved" ? (
          <div className="space-y-3">
            <p>
              These {chosen.length} {chosen.length === 1 ? "action is" : "actions are"} handed to your
              automation (n8n) right away. This can't be undone from here.
            </p>
            <ul className="bg-surface2 list-disc space-y-1 rounded-lg p-3 pl-7">
              {chosen.map((description) => (
                <li key={description}>{description}</li>
              ))}
            </ul>
          </div>
        ) : (
          <p>No actions will be taken, and this incident will be closed as rejected.</p>
        )}
      </ConfirmDialog>
    </section>
  );
}
