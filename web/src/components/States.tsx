import { AlertCircle, Inbox } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../lib/cn";

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton", className)} aria-hidden />;
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="bg-dangerbg text-danger flex items-start gap-3 rounded-xl p-4">
      <AlertCircle className="mt-0.5 size-5 shrink-0" aria-hidden />
      <div className="flex-1">
        <p className="font-semibold">Something went wrong</p>
        <p className="mt-1 text-sm">{message}</p>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="mt-3 rounded-lg border border-current px-3 py-1.5 text-sm font-semibold"
          >
            Try again
          </button>
        )}
      </div>
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="bg-surface rounded-xl border border-dashed border-line px-6 py-12 text-center">
      <Inbox className="text-muted mx-auto size-8" aria-hidden />
      <h2 className="mt-3 text-lg font-semibold">{title}</h2>
      <div className="text-muted mx-auto mt-1 max-w-md text-sm">{children}</div>
    </div>
  );
}
