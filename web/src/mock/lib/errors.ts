import { HttpResponse } from "msw";

// Every 4xx and 5xx body is `{error, message, details}` — see the API
// conventions reference. Keep mock errors faithful to that shape, or the SPA's
// error handling is exercised against something the server never sends.

const DERIVED_CODES: Record<number, string> = {
  400: "bad_request",
  401: "unauthorized",
  403: "forbidden",
  404: "not_found",
  409: "conflict",
  422: "unprocessable_content",
  503: "unavailable",
};

/** An error whose machine code is the one the server derives from the status. */
export function httpError(status: number, message: string) {
  return HttpResponse.json(
    {
      error: DERIVED_CODES[status] ?? "internal_error",
      message,
      details: null,
    },
    { status },
  );
}

/** An error carrying a specific machine code the SPA branches on. */
export function validationError(
  error: string,
  message: string,
  status = 422,
  details: Record<string, unknown> | null = null,
) {
  return HttpResponse.json({ error, message, details }, { status });
}
