import { HttpResponse } from "msw";

// FastAPI's default error envelope is `{"detail": ...}`; client.ts reads
// `body.error ?? body.detail`. Keep mock errors faithful to that shape.
export function httpError(status: number, detail: string) {
  return HttpResponse.json({ detail }, { status });
}

// The SQL guard and a few other 422s return a nested detail object
// (`{"detail": {"error", "detail"}}`) — see api/routers/queries.py.
export function validationError(error: string, detail: string) {
  return HttpResponse.json({ detail: { error, detail } }, { status: 422 });
}
