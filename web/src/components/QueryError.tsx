import { KeyRound } from "lucide-react";
import { Link } from "react-router-dom";
import { ApiError } from "../lib/api";
import { ErrorState } from "./States";

/** A 401 is not a failure to retry: it is a prompt to enter the API key. */
export function QueryError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (error instanceof ApiError && error.status === 401) {
    return (
      <div role="alert" className="bg-warnbg text-warn flex items-start gap-3 rounded-xl p-4">
        <KeyRound className="mt-0.5 size-5 shrink-0" aria-hidden />
        <div>
          <p className="font-semibold">This server needs an API key</p>
          <p className="mt-1 text-sm">
            Add the key your administrator gave you in{" "}
            <Link to="/settings" className="font-semibold underline">
              Settings
            </Link>
            , then come back.
          </p>
        </div>
      </div>
    );
  }
  const message = error instanceof Error ? error.message : "Unknown error";
  return <ErrorState message={message} onRetry={onRetry} />;
}
