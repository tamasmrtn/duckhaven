export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * Extract a human-readable message from a parsed error body.
 *
 * FastAPI wraps errors in `detail`. The SQL guard and a few other 422s return a
 * structured object (`{"detail": {"error", "detail"}}`); simpler errors return a
 * string (`{"detail": "..."}`). Unwrap the structured form so we never surface
 * "[object Object]", and fall back through the available fields.
 */
function errorMessage(body: unknown): string | undefined {
  if (body == null || typeof body !== "object") return undefined;
  const { detail, error } = body as { detail?: unknown; error?: unknown };
  if (typeof detail === "string") return detail;
  if (detail != null && typeof detail === "object") {
    const d = detail as { detail?: unknown; error?: unknown };
    if (typeof d.detail === "string") return d.detail;
    if (typeof d.error === "string") return d.error;
    return JSON.stringify(detail);
  }
  if (typeof error === "string") return error;
  return undefined;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });

  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      message = errorMessage(body) ?? message;
    } catch {
      // ignore parse error
    }
    throw new ApiError(res.status, message);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as T;
}

export function get<T>(path: string) {
  return request<T>(path);
}

export function post<T>(path: string, body?: unknown) {
  return request<T>(path, {
    method: "POST",
    body: body != null ? JSON.stringify(body) : undefined,
  });
}

export function del(path: string) {
  return request<void>(path, { method: "DELETE" });
}
