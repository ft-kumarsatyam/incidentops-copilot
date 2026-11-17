import { createSseParser, runStream, type StreamEvent, type StreamState } from "./stream";

describe("createSseParser", () => {
  it("emits complete events and buffers partial ones across chunks", () => {
    const events: StreamEvent[] = [];
    const feed = createSseParser((event) => events.push(event));

    feed('event: incident\ndata: {"type":"incident","run_id":"a"}\n');
    expect(events).toEqual([]);
    feed('\nevent: incident\ndata: {"type":"incident","run_id":"b"}\n\n');

    expect(events.map((e) => e.run_id)).toEqual(["a", "b"]);
  });

  it("ignores keep-alive comments and malformed frames", () => {
    const events: StreamEvent[] = [];
    const feed = createSseParser((event) => events.push(event));

    feed(": keep-alive\n\n");
    feed("data: not json\n\n");
    feed('data: {"type":"ready"}\n\n');

    expect(events).toEqual([{ type: "ready" }]);
  });

  it("handles CRLF line endings", () => {
    const events: StreamEvent[] = [];
    createSseParser((event) => events.push(event))('data: {"type":"ready"}\r\n\r\n');
    expect(events).toHaveLength(1);
  });
});

function bodyOf(text: string): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(text));
      controller.close();
    },
  });
  return new Response(stream, { status: 200 });
}

describe("runStream", () => {
  it("reports live on ready, forwards events, and reconnects after the stream ends", async () => {
    const controller = new AbortController();
    const states: StreamState[] = [];
    const events: StreamEvent[] = [];
    let calls = 0;
    const fetchImpl = vi.fn(async () => {
      calls += 1;
      if (calls === 2) controller.abort();
      return bodyOf('data: {"type":"ready"}\n\ndata: {"type":"incident","run_id":"x"}\n\n');
    });

    await runStream({
      url: "/incidents/stream",
      headers: () => ({ "X-API-Key": "k" }),
      onEvent: (event) => events.push(event),
      onState: (state) => states.push(state),
      signal: controller.signal,
      fetchImpl: fetchImpl as unknown as typeof fetch,
      retryDelayMs: () => 0,
    });

    expect(states).toContain("live");
    expect(states).toContain("reconnecting");
    expect(events.some((e) => e.run_id === "x")).toBe(true);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    const init = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect((init[1].headers as Record<string, string>)["X-API-Key"]).toBe("k");
  });

  it("keeps retrying after an error response instead of giving up", async () => {
    const controller = new AbortController();
    let calls = 0;
    const fetchImpl = vi.fn(async () => {
      calls += 1;
      if (calls === 3) controller.abort();
      return new Response("nope", { status: 401 });
    });

    await runStream({
      url: "/incidents/stream",
      headers: () => ({}),
      onEvent: () => {},
      onState: () => {},
      signal: controller.signal,
      fetchImpl: fetchImpl as unknown as typeof fetch,
      retryDelayMs: () => 0,
    });

    expect(calls).toBe(3);
  });
});
