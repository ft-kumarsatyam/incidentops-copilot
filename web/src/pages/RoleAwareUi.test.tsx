import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { Layout } from "../components/Layout";
import { ToastProvider } from "../hooks/useToast";
import { incident } from "../test/factories";
import { IncidentsPage } from "./IncidentsPage";
import { NewIncidentPage } from "./NewIncidentPage";

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

/** Serve /me for a role, plus the few other endpoints these pages call. */
function signedInAs(roles: string[], permissions: string[], items: unknown[] = []) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/me")) {
        return json({ subject: "s", actor: "dana", tenant: "acme", roles, method: "api-key", permissions });
      }
      if (url.endsWith("/ready")) return json({ status: "ready" });
      return json(items);
    }),
  );
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<IncidentsPage />} />
              <Route path="new" element={<NewIncidentPage />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("role-aware UI", () => {
  it("shows a viewer no way to report an incident", async () => {
    signedInAs(["viewer"], ["incident:read"], [incident({ summary: "Existing one", status: "awaiting_approval" })]);
    renderAt("/");

    expect(await screen.findByTestId("identity-chip")).toHaveTextContent("viewer · acme");
    expect(await screen.findByText("Existing one")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /report/i })).not.toBeInTheDocument();
  });

  it("does not offer samples to a viewer on an empty list", async () => {
    signedInAs(["viewer"], ["incident:read"], []);
    renderAt("/");

    expect(await screen.findByText("Incidents will appear here when someone reports one.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /checkout is failing/i })).not.toBeInTheDocument();
  });

  it("explains why a viewer who opens the report page cannot use it", async () => {
    signedInAs(["viewer"], ["incident:read"]);
    renderAt("/new");

    expect(await screen.findByText("You can't report incidents")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /submit|report incident|send/i })).not.toBeInTheDocument();
  });

  it("shows a responder the report link and the highest role", async () => {
    signedInAs(["viewer", "responder"], ["incident:read", "incident:create"]);
    renderAt("/");

    expect(await screen.findByTestId("identity-chip")).toHaveTextContent("responder · acme");
    expect(screen.getAllByRole("link", { name: /report/i }).length).toBeGreaterThan(0);
  });

  it("shows a custom role by name, after any built-in role", async () => {
    signedInAs(["viewer", "payments-approver"], ["incident:read", "incident:decide"]);
    renderAt("/");

    expect(await screen.findByTestId("identity-chip")).toHaveTextContent("viewer · acme");
    expect(screen.getByTestId("identity-chip")).toHaveAttribute("title", expect.stringContaining("payments-approver"));
  });

  it("falls back to the custom role when it is the only one", async () => {
    signedInAs(["payments-approver"], ["incident:read"]);
    renderAt("/");

    expect(await screen.findByTestId("identity-chip")).toHaveTextContent("payments-approver · acme");
  });
});
