import { describe, it, expect, vi, beforeEach } from "vitest";
import { ApiError, get, post, del } from "@/api/client";

function mockFetch(status: number, body?: unknown) {
  const response = {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response;

  vi.spyOn(global, "fetch").mockResolvedValue(response);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("ApiError", () => {
  it("carries status, message and the machine code", () => {
    const err = new ApiError(404, "not found", "not_found");
    expect(err.status).toBe(404);
    expect(err.message).toBe("not found");
    expect(err.code).toBe("not_found");
    expect(err.name).toBe("ApiError");
    expect(err instanceof Error).toBe(true);
  });
});

describe("get()", () => {
  it("resolves with parsed JSON on 200", async () => {
    mockFetch(200, { hello: "world" });
    const result = await get<{ hello: string }>("/test");
    expect(result).toEqual({ hello: "world" });
  });

  it("resolves to undefined on 204", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      status: 204,
      json: () => Promise.reject(new Error("no body")),
    } as unknown as Response);
    const result = await get("/test");
    expect(result).toBeUndefined();
  });

  it("surfaces the envelope message and machine code on 4xx", async () => {
    mockFetch(401, {
      error: "unauthorized",
      message: "No valid session cookie or bearer token was supplied.",
      details: null,
    });
    await expect(get("/test")).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
      code: "unauthorized",
      message: "No valid session cookie or bearer token was supplied.",
    });
  });

  it("keeps the router's own code rather than the one derived from the status", async () => {
    // Branching on `code` is the point of the envelope: the SQL guard's refusal
    // has to stay distinguishable from any other 422.
    mockFetch(422, {
      error: "sql_not_allowed",
      message: "Disallowed statement type(s): SET",
      details: null,
    });
    await expect(get("/test")).rejects.toMatchObject({
      status: 422,
      code: "sql_not_allowed",
      message: "Disallowed statement type(s): SET",
    });
  });

  it("exposes structured details for the errors documented to carry them", async () => {
    mockFetch(409, {
      error: "dataset_in_use",
      message: "'orders' still has metric 'revenue'.",
      details: { dependents: ["metric 'revenue'"] },
    });
    await expect(get("/test")).rejects.toMatchObject({
      code: "dataset_in_use",
      details: { dependents: ["metric 'revenue'"] },
    });
  });

  it('falls back to "HTTP {status}" for a body that is not the envelope', async () => {
    mockFetch(500, { unexpected: true });
    await expect(get("/test")).rejects.toMatchObject({
      status: 500,
      message: "HTTP 500",
    });
  });

  it('falls back to "HTTP 500" when body is not JSON', async () => {
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: false,
      status: 500,
      json: () => Promise.reject(new SyntaxError("unexpected token")),
    } as unknown as Response);
    await expect(get("/test")).rejects.toMatchObject({
      name: "ApiError",
      status: 500,
      message: "HTTP 500",
    });
  });
});

describe("post()", () => {
  it("sends JSON body with correct Content-Type", async () => {
    const fetchSpy = vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ id: 1 }),
    } as unknown as Response);

    await post("/items", { name: "test" });

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/items",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ name: "test" }),
        headers: expect.objectContaining({
          "Content-Type": "application/json",
        }),
      }),
    );
  });
});

describe("del()", () => {
  it("uses DELETE method", async () => {
    const fetchSpy = vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      status: 204,
      json: () => Promise.reject(new Error("no body")),
    } as unknown as Response);

    await del("/items/1");

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/items/1",
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});
