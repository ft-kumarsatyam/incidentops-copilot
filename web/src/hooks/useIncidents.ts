import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useSettings } from "../lib/settings";
import type { Decision, Incident, NewIncident } from "../lib/types";
import { useLiveState } from "./useLive";

/** Poll only while the live stream is down, so the UI never silently goes stale. */
function useFallbackInterval(): number | false {
  return useLiveState() === "live" ? false : 15_000;
}

/** Who the server thinks you are. Re-fetched when the credential changes. */
export function useMe() {
  const { apiKey } = useSettings();
  return useQuery({ queryKey: ["me", apiKey], queryFn: () => api.me(), retry: false, staleTime: 60_000 });
}

/** Whether the signed-in role may do something. Optimistic until /me answers: the server
 * enforces every permission anyway, this only decides whether to show a button. */
export function useCan(): (permission: string) => boolean {
  const { data } = useMe();
  return (permission) => (data?.permissions ? data.permissions.includes(permission) : true);
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

export function useRetryRun(runId: string) {
  const refresh = useRefresh();
  return useMutation({ mutationFn: () => api.retryRun(runId), onSuccess: refresh });
}
