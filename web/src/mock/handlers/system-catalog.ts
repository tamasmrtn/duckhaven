import { http, HttpResponse } from "msw";

import { systemCatalog } from "../fixtures/systemCatalog";

/** `GET /api/workspaces/:ws/system-catalog`: the fixed tables, for every workspace. */
export const systemCatalogHandlers = [
  http.get("/api/workspaces/:ws/system-catalog", () =>
    HttpResponse.json(systemCatalog),
  ),
];
