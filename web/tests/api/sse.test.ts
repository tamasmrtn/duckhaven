import { describe, expect, it } from "vitest";
import { ApiError } from "@/api/client";
import { readSSE } from "@/api/sse";

function streamOf(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, {
    headers: { "Content-Type": "text/event-stream" },
  });
}

async function all<T>(gen: AsyncGenerator<T>): Promise<T[]> {
  const out: T[] = [];
  for await (const item of gen) out.push(item);
  return out;
}

describe("readSSE", () => {
  it("yields each data line, however the chunks split it, and skips pings", async () => {
    const res = streamOf([
      'data: {"n":',
      '1}\n\n: ping\n\ndata: {"n":2}\n',
      "\n",
    ]);

    expect(await all(readSSE<{ n: number }>(res))).toEqual([
      { n: 1 },
      { n: 2 },
    ]);
  });

  it("throws the error envelope of a stream that never opened", async () => {
    const res = new Response(
      JSON.stringify({
        error: "unknown_widget",
        message: "No widget 'x'.",
        details: null,
      }),
      { status: 422 },
    );

    const error = await all(readSSE(res)).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 422,
      code: "unknown_widget",
      message: "No widget 'x'.",
    });
  });
});
