/** Minimal Server-Sent Events client built on fetch, so it can send the X-API-Key header. */

export interface StreamEvent {
  type: string;
  [key: string]: unknown;
}

/** Incremental SSE parser: feed it text chunks, get complete events back. */
export function createSseParser(onEvent: (event: StreamEvent) => void) {
  let buffer = "";
  return (chunk: string) => {
    buffer += chunk.replace(/\r\n/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const data = frame
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (data) {
        try {
          onEvent(JSON.parse(data) as StreamEvent);
        } catch {
          /* ignore a malformed frame rather than dropping the connection */
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
  };
}

export type StreamState = "connecting" | "live" | "reconnecting";

export interface StreamOptions {
  url: string;
  headers: () => Record<string, string>;
  onEvent: (event: StreamEvent) => void;
  onState: (state: StreamState) => void;
  signal: AbortSignal;
  /** Injected in tests. */
  fetchImpl?: typeof fetch;
  retryDelayMs?: (attempt: number) => number;
}

const defaultDelay = (attempt: number) => Math.min(30_000, 1_000 * 2 ** attempt);

/** Keeps a stream open until `signal` aborts, reconnecting with backoff. */
export async function runStream(options: StreamOptions): Promise<void> {
  const { url, headers, onEvent, onState, signal } = options;
  const doFetch = options.fetchImpl ?? fetch;
  const delay = options.retryDelayMs ?? defaultDelay;
  let attempt = 0;

  while (!signal.aborted) {
    onState(attempt === 0 ? "connecting" : "reconnecting");
    try {
      const response = await doFetch(url, {
        headers: { Accept: "text/event-stream", ...headers() },
        signal,
      });
      if (!response.ok || !response.body) throw new Error(`stream ${response.status}`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      const feed = createSseParser((event) => {
        if (event.type === "ready") {
          attempt = 0;
          onState("live");
        }
        onEvent(event);
      });
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        feed(decoder.decode(value, { stream: true }));
      }
    } catch {
      if (signal.aborted) return;
    }
    if (signal.aborted) return;
    onState("reconnecting");
    await new Promise((resolve) => setTimeout(resolve, delay(attempt)));
    attempt += 1;
  }
}
