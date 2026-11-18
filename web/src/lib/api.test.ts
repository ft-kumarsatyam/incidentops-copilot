import { api, ApiError } from "./api";
import { saveSettings } from "./settings";

const json = (body: unknown, init: ResponseInit = {}) =>
  new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" }, ...init });

beforeEach(() => {
  saveSettings({ apiKey: "", actor: "", remember: false });
});

afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("sends the API key and the acting user's name", async () => {
    saveSettings({ apiKey: "secret", actor: "Ana", remember: false });
    const fetchMock = vi.fn(async () => json([]));
    vi.stubGlobal("fetch", fetchMock);

    await api.list();

    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.headers).toMatchObject({ "X-API-Key": "secret", "X-Actor": "Ana" });
  });

  it.each([
    ["iops_0123456789ab_secretvalue", "scoped key"],
    ["eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1In0.c2ln", "SSO token"],
  ])("sends a %2$s as a bearer token, not as the shared key", async (credential) => {
    saveSettings({ apiKey: credential, actor: "", remember: false });
    const fetchMock = vi.fn(async () => json([]));
    vi.stubGlobal("fetch", fetchMock);

    await api.list();

    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.headers).toMatchObject({ Authorization: `Bearer ${credential}` });
    expect(init.headers).not.toHaveProperty("X-API-Key");
  });

  it("reads who the server thinks you are", async () => {
    const me = { subject: "s", actor: "ana", tenant: "acme", roles: ["viewer"], method: "oidc", permissions: [] };
    vi.stubGlobal("fetch", vi.fn(async () => json(me)));

    expect(await api.me()).toEqual(me);
  });

  it("omits auth headers when no key is configured", async () => {
    const fetchMock = vi.fn(async () => json([]));
    vi.stubGlobal("fetch", fetchMock);

    await api.list();

    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.headers).not.toHaveProperty("X-API-Key");
  });

  it("turns server error details into readable messages", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "run is not awaiting approval" }, { status: 409 })));

    await expect(api.get("x")).rejects.toMatchObject({ status: 409, message: "run is not awaiting approval" });
  });

  it("flattens validation errors", async () => {
    const detail = [{ msg: "String should have at least 1 character" }];
    vi.stubGlobal("fetch", vi.fn(async () => json({ detail }, { status: 422 })));

    await expect(api.get("x")).rejects.toThrow("String should have at least 1 character");
  });

  it("reports an unreachable server in plain language", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));

    const failure = await api.list().catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 0, message: expect.stringContaining("Can't reach the server") });
  });

  it("encodes run ids in the URL and posts the idempotency key with a decision", async () => {
    const fetchMock = vi.fn(async () => json({}));
    vi.stubGlobal("fetch", fetchMock);
    const { incident } = await import("../test/factories");

    await api.decide(incident({ run_id: "a/b", idempotency_key: "key-1" }), {
      decision: "approved",
      approved_actions: ["Do it"],
    });

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/incidents/a%2Fb/decision");
    expect(JSON.parse(init.body as string)).toEqual({
      idempotency_key: "key-1",
      decision: "approved",
      approved_actions: ["Do it"],
    });
  });
});
