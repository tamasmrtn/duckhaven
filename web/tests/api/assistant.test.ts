import { afterEach, describe, expect, it, vi } from "vitest";
import { streamMessage } from "@/api/assistant";

async function sentBody(
  opts: Parameters<typeof streamMessage>[3],
): Promise<Record<string, unknown>> {
  const fetch = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(
      new Response("", { headers: { "Content-Type": "text/event-stream" } }),
    );
  for await (const frame of streamMessage("ws", "c1", "hi", opts)) {
    void frame;
  }
  const init = fetch.mock.calls[0][1] as RequestInit;
  return JSON.parse(init.body as string) as Record<string, unknown>;
}

describe("streamMessage", () => {
  afterEach(() => vi.restoreAllMocks());

  it("sends the worksheet as the page the turn came from", async () => {
    const body = await sentBody({
      editorSql: "SELECT 1",
      selectionSql: "1",
      catalog: "main",
    });

    expect(body).toEqual({
      prompt: "hi",
      catalog: "main",
      page_context: { page: "worksheet", sql: "SELECT 1", selection: "1" },
    });
  });

  it("sends no page from outside a worksheet", async () => {
    expect((await sentBody({})).page_context).toBeNull();
  });
});
