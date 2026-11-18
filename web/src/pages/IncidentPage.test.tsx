import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ToastProvider } from "../hooks/useToast";
import type { Incident } from "../lib/types";
import { incident } from "../test/factories";
import { IncidentPage } from "./IncidentPage";

const me = {
  subject: "s",
  actor: "ana",
  tenant: "default",
  roles: ["responder"],
  method: "api-key",
  permissions: ["incident:read", "incident:create", "incident:retry"],
};

/** Serve the incident, its audit trail and a retry endpoint; remember every request made. */
function mockServer(initial: Incident, afterRetry: Incident) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(`${init?.method ?? "GET"} ${url}`);
      const json = (body: unknown) =>
        new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
      if (url.endsWith("/me")) return json(me);
      if (url.endsWith("/retry-run")) return json(afterRetry);
      if (url.endsWith("/audit")) return json([]);
      return json(calls.some((c) => c.includes("retry-run")) ? afterRetry : initial);
    }),
  );
  return calls;
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={["/incidents/inc-1"]}>
          <Routes>
            <Route path="incidents/:runId" element={<IncidentPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("IncidentPage: runs that did not finish analysis", () => {
  it("explains a failed analysis with its reason and lets you retry it", async () => {
    const errored = incident({ status: "errored", error: "RuntimeError: provider outage", proposed_actions: [] });
    const calls = mockServer(errored, incident({ status: "queued", proposed_actions: [] }));
    renderPage();

    expect(await screen.findByText("The analysis could not be completed")).toBeInTheDocument();
    expect(within(screen.getByRole("alert")).getByText(/provider outage/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /retry analysis/i }));

    await waitFor(() => expect(calls).toContain("POST /incidents/inc-1/retry-run"));
    // Once it is queued again the failure banner goes away.
    await waitFor(() => expect(screen.queryByText("The analysis could not be completed")).not.toBeInTheDocument());
  });

  it("shows no retry button for a healthy queued run", async () => {
    mockServer(incident({ status: "queued", proposed_actions: [] }), incident());
    renderPage();

    expect(await screen.findAllByText("Queued")).not.toHaveLength(0);
    expect(screen.queryByRole("button", { name: /retry analysis/i })).not.toBeInTheDocument();
  });
});

describe("IncidentPage: roles", () => {
  it("hides the retry button from someone who may not retry", async () => {
    const errored = incident({ status: "errored", error: "boom", proposed_actions: [] });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const json = (body: unknown) =>
          new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
        if (url.endsWith("/me")) return json({ ...me, roles: ["viewer"], permissions: ["incident:read"] });
        if (url.endsWith("/audit")) return json([]);
        return json(errored);
      }),
    );
    renderPage();

    expect(await screen.findByText(/analysis could not be completed/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("button", { name: /retry analysis/i })).not.toBeInTheDocument());
  });
});
