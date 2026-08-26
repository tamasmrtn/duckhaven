export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    /** Stable machine code from the response body, for branching on. */
    public code?: string,
    public details?: Record<string, unknown> | null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * Every 4xx and 5xx body is `{error, message, details}` -- see the API
 * conventions reference. `error` is the stable machine code to branch on;
 * `message` is what a person should read.
 */
export interface ApiErrorBody {
  error: string;
  message: string;
  details?: Record<string, unknown> | null;
}

function parseError(body: unknown): ApiErrorBody | undefined {
  if (body == null || typeof body !== "object") return undefined;
  const { error, message, details } = body as Partial<ApiErrorBody>;
  if (typeof error !== "string" || typeof message !== "string")
    return undefined;
  return { error, message, details: details ?? null };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });

  if (!res.ok) {
    let parsed: ApiErrorBody | undefined;
    try {
      parsed = parseError(await res.json());
    } catch {
      // A body that is absent or not JSON leaves the status as the only signal.
    }
    throw new ApiError(
      res.status,
      parsed?.message ?? `HTTP ${res.status}`,
      parsed?.error,
      parsed?.details,
    );
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

/**
 * POST a body that is already text rather than a value to serialize.
 *
 * Used for document uploads — a semantic YAML file — where JSON-encoding the
 * text would make the server parse a JSON string and then parse YAML out of it,
 * and would mangle the line numbers in any parse error the user is shown.
 */
export function postText<T>(path: string, body: string) {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "text/plain" },
    body,
  });
}

export function put<T>(path: string, body?: unknown) {
  return request<T>(path, {
    method: "PUT",
    body: body != null ? JSON.stringify(body) : undefined,
  });
}

export function patch<T>(path: string, body?: unknown) {
  return request<T>(path, {
    method: "PATCH",
    body: body != null ? JSON.stringify(body) : undefined,
  });
}

export function del(path: string) {
  return request<void>(path, { method: "DELETE" });
}

/**
 * A paged collection: `{items, cursor, has_more}` — see the API conventions
 * reference.
 */
export interface Page<T> {
  items: T[];
  cursor: string | null;
  has_more: boolean;
}

/** How many pages `getAllPages` will follow before giving up. */
const MAX_PAGES = 50;

/**
 * Read a paged collection in full, following the cursor.
 *
 * These lists were unbounded before the server started paging them, and the
 * screens that render them show every row — an admin who cannot see a user
 * because the list stopped at 100 has no way to tell. Rather than truncate
 * silently, walk the pages; the request count is the price of the same answer
 * the endpoint used to give in one.
 *
 * `MAX_PAGES` stops a runaway loop if a server ever returns `has_more` with no
 * cursor. Screens that grow a real "load more" should page explicitly instead.
 */
export async function getAllPages<T>(
  path: string,
  params: Record<string, string> = {},
): Promise<T[]> {
  const items: T[] = [];
  let cursor: string | null = null;

  // The path may already carry a filter (a repeated `status`, which cannot go
  // through the flat `params` object), so merge rather than append.
  const [base, existing] = path.split("?");

  for (let i = 0; i < MAX_PAGES; i++) {
    const query = new URLSearchParams(existing);
    for (const [k, v] of Object.entries(params)) query.set(k, v);
    query.set("limit", "1000");
    if (cursor) query.set("cursor", cursor);
    const page: Page<T> = await get<Page<T>>(`${base}?${query}`);
    items.push(...page.items);
    if (!page.has_more || !page.cursor) break;
    cursor = page.cursor;
  }
  return items;
}
