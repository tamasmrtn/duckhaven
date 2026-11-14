import { ApiError } from "./client";
import type { ApiErrorBody } from "./client";

/**
 * Read a `text/event-stream` body and yield each `data:` line, decoded as JSON.
 *
 * A response that is not a stream (an error status, or no body) carries the
 * usual `{error, message, details}` envelope instead, and throws it as an
 * `ApiError`, so a request refused before its stream opened reads like any
 * other refused request.
 */
export async function* readSSE<T>(
  res: Response,
  signal?: AbortSignal,
): AsyncGenerator<T, void, unknown> {
  if (!res.ok || !res.body) {
    // The stream never opened, so the body is an ordinary error envelope.
    let body: Partial<ApiErrorBody> | undefined;
    try {
      body = (await res.json()) as Partial<ApiErrorBody>;
    } catch {
      // A body that is absent or not JSON leaves the status as the only signal.
    }
    throw new ApiError(
      res.status,
      body?.message ?? `HTTP ${res.status}`,
      body?.error,
      body?.details,
    );
  }
  const reader = res.body.getReader();
  // Cancelling the reader unblocks a pending read() so Stop takes effect
  // immediately, even where aborting the fetch doesn't reject the read.
  const onAbort = () => void reader.cancel().catch(() => {});
  signal?.addEventListener("abort", onAbort);
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        for (const line of block.split("\n")) {
          if (line.startsWith("data: ")) {
            yield JSON.parse(line.slice(6)) as T;
          }
        }
      }
    }
  } finally {
    signal?.removeEventListener("abort", onAbort);
  }
}

/** POST `body` as JSON to `path` and yield the events it streams back. */
export async function* postSSE<T>(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): AsyncGenerator<T, void, unknown> {
  const res = await fetch(`/api${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  yield* readSSE<T>(res, signal);
}
