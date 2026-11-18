import { getSettings } from "./settings";
import type { AuditEntry, Decision, Incident, Me, NewIncident } from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Scoped keys (iops_...) and SSO access tokens (JWTs) go in `Authorization: Bearer`; the
 * legacy shared key stays in `X-API-Key`. */
export function isBearerCredential(credential: string): boolean {
  return credential.startsWith("iops_") || /^eyJ[\w-]+\.[\w-]+\.[\w-]*$/.test(credential);
}

export function authHeaders(): Record<string, string> {
  const { apiKey, actor } = getSettings();
  const headers: Record<string, string> = {};
  if (apiKey) {
    if (isBearerCredential(apiKey)) headers["Authorization"] = `Bearer ${apiKey}`;
    else headers["X-API-Key"] = apiKey;
  }
  // Named credentials carry their own identity, so the server ignores this label for them.
  if (actor) headers["X-Actor"] = actor;
  return headers;
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      return body.detail.map((d: { msg?: string }) => d.msg ?? "invalid value").join("; ");
    }
  } catch {
    /* not JSON */
  }
  return response.statusText || `Request failed (${response.status})`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<{ data: T; total?: number }> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", ...authHeaders(), ...init.headers },
    });
  } catch {
    throw new ApiError(0, "Can't reach the server. Check that it is running.");
  }
  if (!response.ok) throw new ApiError(response.status, await errorMessage(response));
  const total = response.headers.get("X-Total-Count");
  return { data: (await response.json()) as T, total: total ? Number(total) : undefined };
}

export const api = {
  async me(): Promise<Me> {
    return (await request<Me>("/me")).data;
  },
  async list(limit = 200): Promise<Incident[]> {
    return (await request<Incident[]>(`/incidents?limit=${limit}`)).data;
  },
  async get(runId: string): Promise<Incident> {
    return (await request<Incident>(`/incidents/${encodeURIComponent(runId)}`)).data;
  },
  async audit(runId: string): Promise<AuditEntry[]> {
    return (await request<AuditEntry[]>(`/incidents/${encodeURIComponent(runId)}/audit`)).data;
  },
  async create(body: NewIncident): Promise<Incident> {
    return (await request<Incident>("/incidents", { method: "POST", body: JSON.stringify(body) })).data;
  },
  async decide(incident: Incident, decision: Decision): Promise<Incident> {
    const body = { idempotency_key: incident.idempotency_key, ...decision };
    return (
      await request<Incident>(`/incidents/${encodeURIComponent(incident.run_id)}/decision`, {
        method: "POST",
        body: JSON.stringify(body),
      })
    ).data;
  },
  async retryDispatch(runId: string): Promise<Incident> {
    return (
      await request<Incident>(`/incidents/${encodeURIComponent(runId)}/retry-dispatch`, {
        method: "POST",
      })
    ).data;
  },
  async retryRun(runId: string): Promise<Incident> {
    return (
      await request<Incident>(`/incidents/${encodeURIComponent(runId)}/retry-run`, {
        method: "POST",
      })
    ).data;
  },
  async ready(): Promise<{ status: string; checks: Record<string, string> }> {
    const response = await fetch("/ready");
    return (await response.json()) as { status: string; checks: Record<string, string> };
  },
};
