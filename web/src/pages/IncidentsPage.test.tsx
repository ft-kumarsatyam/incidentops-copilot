import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { ToastProvider } from "../hooks/useToast";
import type { Incident } from "../lib/types";
import { incident } from "../test/factories";
import { buildIncident } from "./NewIncidentPage";
import { IncidentsPage } from "./IncidentsPage";

function mockServer(body: unknown, status = 200) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })),
  );
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter>
          <IncidentsPage />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("IncidentsPage", () => {
  it("welcomes a first-time user with sample incidents", async () => {
    mockServer([]);
    renderPage();

    expect(await screen.findByText("No incidents yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /checkout is failing/i })).toBeInTheDocument();
  });

  it("says how many incidents need attention and lists them first", async () => {
    const items: Incident[] = [
      incident({ run_id: "done", summary: "Old resolved one", status: "succeeded" }),
      incident({ run_id: "wait", summary: "Waiting on you", status: "awaiting_approval" }),
    ];
    mockServer(items);
    renderPage();

    expect(await screen.findByText("1 incident needs your attention.")).toBeInTheDocument();
    // Resolved incidents are tucked away by default.
    expect(screen.queryByText("Old resolved one")).not.toBeInTheDocument();
    expect(screen.getByText("Waiting on you")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: /resolved/i }));
    expect(screen.getByText("Old resolved one")).toBeInTheDocument();
  });

  it("filters with the search box", async () => {
    mockServer([
      incident({ run_id: "a", summary: "Checkout down", service: "checkout-api" }),
      incident({ run_id: "b", summary: "Login slow", service: "auth-api" }),
    ]);
    renderPage();
    await screen.findByText("Checkout down");

    await userEvent.type(screen.getByRole("searchbox"), "auth");

    expect(screen.queryByText("Checkout down")).not.toBeInTheDocument();
    expect(screen.getByText("Login slow")).toBeInTheDocument();
  });

  it("asks for an API key on a 401 instead of showing a generic error", async () => {
    mockServer({ detail: "invalid API key" }, 401);
    renderPage();

    await waitFor(() => expect(screen.getByText("This server needs an API key")).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
  });
});

describe("buildIncident", () => {
  const form = (values: Record<string, string>) => {
    const data = new FormData();
    Object.entries(values).forEach(([key, value]) => data.append(key, value));
    return data;
  };

  it("builds a minimal report from just a description", () => {
    const result = buildIncident(form({ summary: "  It broke " }), "", 1_700_000_000_000);

    expect(result).toEqual({
      channel_id: "C-WEB",
      thread_ts: "1700000000.0000",
      reporter: "web-user",
      summary: "It broke",
    });
  });

  it("converts the optional details into the shapes the API expects", () => {
    const result = buildIncident(
      form({
        summary: "x",
        service: "checkout-api",
        symptoms: "12% errors\n\n  slow  \n",
        error_rate: "12",
        repo: "acme/checkout",
        deploy: "abc123",
      }),
      "Ana",
    );

    expect(result.reporter).toBe("Ana");
    expect(result.symptoms).toEqual(["12% errors", "slow"]);
    expect(result.monitoring).toEqual({ service: "checkout-api", error_rate: 0.12 });
    expect(result.github).toEqual({ repo: "acme/checkout", recent_deploy: "abc123" });
  });

  it("caps an out-of-range error rate", () => {
    expect(buildIncident(form({ summary: "x", error_rate: "250" }), "").monitoring).toEqual({ error_rate: 1 });
  });
});
