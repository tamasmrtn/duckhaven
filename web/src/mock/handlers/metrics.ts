import { http, HttpResponse } from "msw";
import { METRICS } from "../fixtures/metrics";

export const metricsHandlers = [
  http.get("/api/admin/agents/metrics", () => {
    return HttpResponse.json(METRICS);
  }),
];
