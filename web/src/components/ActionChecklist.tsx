import { cn } from "../lib/cn";
import { evidenceLabel } from "../lib/format";
import type { Incident } from "../lib/types";

interface Props {
  incident: Incident;
  /** When set, actions are checkboxes the commander can narrow before approving. */
  selected?: ReadonlySet<string>;
  onToggle?: (description: string) => void;
  disabled?: boolean;
}

export function ActionChecklist({ incident, selected, onToggle, disabled }: Props) {
  const evidenceFor = new Map(incident.action_items.map((item) => [item.description, item]));
  const editable = Boolean(selected && onToggle);
  const approved = new Set(incident.approved_actions);

  return (
    <ul className="divide-y divide-line rounded-lg border border-line">
      {incident.proposed_actions.map((description) => {
        const item = evidenceFor.get(description);
        const checked = editable ? selected!.has(description) : approved.has(description);
        return (
          <li key={description} className="flex gap-3 p-3">
            {editable ? (
              <input
                id={`action-${description}`}
                type="checkbox"
                checked={checked}
                disabled={disabled}
                onChange={() => onToggle!(description)}
                className="accent-brand mt-1 size-4 shrink-0"
              />
            ) : null}
            <div className="min-w-0 flex-1">
              <label
                htmlFor={editable ? `action-${description}` : undefined}
                className={cn("block text-sm font-medium", !checked && !editable && approved.size > 0 && "text-muted line-through")}
              >
                {description}
              </label>
              {item && item.evidence.length > 0 && (
                <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
                  <span className="text-muted">Based on:</span>
                  {item.evidence.map((ref) => (
                    <span key={ref} className="bg-neutralbg rounded px-1.5 py-0.5 font-medium">
                      {evidenceLabel(ref)}
                    </span>
                  ))}
                </p>
              )}
            </div>
            {!editable && approved.size > 0 && (
              <span className={cn("shrink-0 text-xs font-semibold", checked ? "text-ok" : "text-muted")}>
                {checked ? "Approved" : "Not approved"}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}
