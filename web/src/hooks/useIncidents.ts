import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Decision, Incident, NewIncident } from "../lib/types";
import { useLiveState } from "./useLive";

/** Poll only while the live stream is down, so the UI never silently goes stale. */
function useFallbackInterval(): number | false {
  return useLiveState() === "live" ? false : 15_000;
}

export function useIncidents() {
  const refetchInterval = useFallbackInterval();
  return useQuery({ queryKey: ["incidents"], queryFn: () => api.list(), refetchInterval });
}

export function useIncident(runId: string) {
  const refetchInterval = useFallbackInterval();
  return useQuery({ queryKey: ["incident", runId], queryFn: () => api.get(runId), refetchInterval });
}

export function useAudit(runId: string) {
  return useQuery({ queryKey: ["audit", runId], queryFn: () => api.audit(runId) });
}

function useRefresh() {
  const client = useQueryClient();
  return (incident: Incident) => {
    client.setQueryData(["incident", incident.run_id], incident);
    void client.invalidateQueries({ queryKey: ["incidents"] });
    void client.invalidateQueries({ queryKey: ["audit", incident.run_id] });
  };
}

export function useCreateIncident() {
  const refresh = useRefresh();
  return useMutation({ mutationFn: (body: NewIncident) => api.create(body), onSuccess: refresh });
}

export function useDecide(incident: Incident) {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: (decision: Decision) => api.decide(incident, decision),
    onSuccess: refresh,
  });
}

export function useRetryDispatch(runId: string) {
  const refresh = useRefresh();
  return useMutation({ mutationFn: () => api.retryDispatch(runId), onSuccess: refresh });
}
