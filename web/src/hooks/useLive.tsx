import { useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { authHeaders } from "../lib/api";
import { runStream, type StreamState } from "../lib/stream";
import { useSettings } from "../lib/settings";

const LiveContext = createContext<StreamState>("connecting");

/** Opens one SSE connection for the whole app and refreshes cached data when runs change. */
export function LiveProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const { apiKey } = useSettings();
  const [state, setState] = useState<StreamState>("connecting");

  useEffect(() => {
    const controller = new AbortController();
    void runStream({
      url: "/incidents/stream",
      headers: authHeaders,
      signal: controller.signal,
      onState: setState,
      onEvent: (event) => {
        if (event.type !== "incident") return;
        void client.invalidateQueries({ queryKey: ["incidents"] });
        if (typeof event.run_id === "string") {
          void client.invalidateQueries({ queryKey: ["incident", event.run_id] });
          void client.invalidateQueries({ queryKey: ["audit", event.run_id] });
        }
      },
    });
    return () => controller.abort();
    // Reconnect when the API key changes: the stream is authenticated.
  }, [client, apiKey]);

  return <LiveContext.Provider value={state}>{children}</LiveContext.Provider>;
}

export const useLiveState = () => useContext(LiveContext);
