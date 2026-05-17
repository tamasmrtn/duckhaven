import { http, HttpResponse } from "msw";
import { SCHEMAS, generateSampleRows } from "../fixtures/schemas";
import { findWorkspace } from "../fixtures/workspaces";

export const schemaHandlers = [
  http.get("/api/workspaces/:ws/schemas", ({ params }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return new HttpResponse(null, { status: 404 });
    const schemas = SCHEMAS[ws.id] ?? [];
    return HttpResponse.json(
      schemas.map((s) => ({ name: s.name, workspace_id: s.workspace_id })),
    );
  }),

  http.get("/api/workspaces/:ws/schemas/:schema/tables", ({ params }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return new HttpResponse(null, { status: 404 });
    const schema = (SCHEMAS[ws.id] ?? []).find((s) => s.name === params.schema);
    if (!schema) return new HttpResponse(null, { status: 404 });
    return HttpResponse.json(schema.tables);
  }),

  http.get(
    "/api/workspaces/:ws/schemas/:schema/tables/:table",
    ({ params }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return new HttpResponse(null, { status: 404 });
      const schema = (SCHEMAS[ws.id] ?? []).find(
        (s) => s.name === params.schema,
      );
      if (!schema) return new HttpResponse(null, { status: 404 });
      const table = schema.tables.find((t) => t.name === params.table);
      if (!table) return new HttpResponse(null, { status: 404 });
      return HttpResponse.json(table);
    },
  ),

  http.get(
    "/api/workspaces/:ws/schemas/:schema/tables/:table/sample",
    ({ params }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return new HttpResponse(null, { status: 404 });
      const schema = (SCHEMAS[ws.id] ?? []).find(
        (s) => s.name === params.schema,
      );
      if (!schema) return new HttpResponse(null, { status: 404 });
      const table = schema.tables.find((t) => t.name === params.table);
      if (!table) return new HttpResponse(null, { status: 404 });
      const rows = generateSampleRows(table, 20);
      return HttpResponse.json({
        rows,
        columns: table.columns.map((c) => c.name),
        cursor: null,
        total: table.row_count ?? rows.length,
      });
    },
  ),
];
