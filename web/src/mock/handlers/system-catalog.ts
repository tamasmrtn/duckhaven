import { http, HttpResponse } from "msw";

import { systemCatalog } from "../fixtures/systemCatalog";
import { generateSampleRows } from "../fixtures/schemas";
import { httpError } from "../lib/errors";

/** `GET /api/workspaces/:ws/system-catalog`: the fixed tables, for every
 * workspace, and a preview of each. */
export const systemCatalogHandlers = [
  http.get("/api/workspaces/:ws/system-catalog", () =>
    HttpResponse.json(systemCatalog),
  ),
  http.get(
    "/api/workspaces/:ws/system-catalog/schemas/:schema/tables/:table/sample",
    ({ params }) => {
      const table = systemCatalog.tables.find(
        (t) => t.schema_name === params.schema && t.name === params.table,
      );
      if (!table) return httpError(404, "Not Found");
      const rows = generateSampleRows(table, 20);
      return HttpResponse.json({
        rows,
        columns: table.columns.map((c) => c.name),
        cursor: null,
        total: rows.length,
      });
    },
  ),
];
