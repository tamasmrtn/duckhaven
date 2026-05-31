import { http, HttpResponse } from "msw";
import { SCHEMAS, generateSampleRows } from "../fixtures/schemas";
import { findWorkspace } from "../fixtures/workspaces";
import { httpError } from "../lib/errors";
import type { CatalogTable, TableColumn } from "@/types/catalog";

export const schemaHandlers = [
  http.get("/api/workspaces/:ws/schemas", ({ params }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    const schemas = SCHEMAS[ws.id] ?? [];
    return HttpResponse.json(
      schemas.map((s) => ({ name: s.name, workspace_id: s.workspace_id })),
    );
  }),

  http.get("/api/workspaces/:ws/schemas/:schema/tables", ({ params }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    const schema = (SCHEMAS[ws.id] ?? []).find((s) => s.name === params.schema);
    if (!schema) return httpError(404, "Schema not found");
    return HttpResponse.json(schema.tables);
  }),

  http.get(
    "/api/workspaces/:ws/schemas/:schema/tables/:table",
    ({ params }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return httpError(404, "Workspace not found");
      const schema = (SCHEMAS[ws.id] ?? []).find(
        (s) => s.name === params.schema,
      );
      if (!schema) return httpError(404, "Schema not found");
      const table = schema.tables.find((t) => t.name === params.table);
      if (!table) return httpError(404, "Table not found");
      return HttpResponse.json(table);
    },
  ),

  http.post("/api/workspaces/:ws/schemas", async ({ params, request }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    const body = (await request.json()) as { name: string };
    const list = SCHEMAS[ws.id] ?? (SCHEMAS[ws.id] = []);
    if (list.some((s) => s.name === body.name)) {
      return httpError(409, "Schema exists");
    }
    list.push({ name: body.name, workspace_id: ws.id, tables: [] });
    return HttpResponse.json(
      { name: body.name, catalog_name: ws.slug },
      { status: 201 },
    );
  }),

  http.post(
    "/api/workspaces/:ws/schemas/:schema/tables",
    async ({ params, request }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return httpError(404, "Workspace not found");
      const list = SCHEMAS[ws.id] ?? (SCHEMAS[ws.id] = []);
      const schema = list.find((s) => s.name === params.schema);
      if (!schema) return httpError(404, "Schema not found");
      const body = (await request.json()) as {
        name: string;
        columns: { name: string; type: string; nullable: boolean }[];
      };
      if (schema.tables.some((t) => t.name === body.name)) {
        return httpError(409, "Table exists");
      }
      const columns: TableColumn[] = body.columns.map((c, i) => ({
        position: i + 1,
        name: c.name,
        type: c.type,
        nullable: c.nullable,
      }));
      const created: CatalogTable = {
        name: body.name,
        schema_name: schema.name,
        workspace_id: ws.id,
        row_count: 0,
        size_bytes: 0,
        format: "Delta",
        catalog_commits: true,
        owner: "you",
        last_write_at: null,
        last_write_by: null,
        last_write_agent: null,
        columns,
      };
      schema.tables.push(created);
      return HttpResponse.json(created, { status: 201 });
    },
  ),

  http.delete(
    "/api/workspaces/:ws/schemas/:schema/tables/:table",
    ({ params }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return httpError(404, "Workspace not found");
      const schema = (SCHEMAS[ws.id] ?? []).find(
        (s) => s.name === params.schema,
      );
      if (!schema) return httpError(404, "Schema not found");
      const idx = schema.tables.findIndex((t) => t.name === params.table);
      if (idx === -1) return httpError(404, "Table not found");
      schema.tables.splice(idx, 1);
      return new HttpResponse(null, { status: 204 });
    },
  ),

  // No backend route yet; renders the table-detail preview (CatalogPage). Kept
  // deterministic and flagged for backend enrichment — see plan §H.
  http.get(
    "/api/workspaces/:ws/schemas/:schema/tables/:table/sample",
    ({ params }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return httpError(404, "Workspace not found");
      const schema = (SCHEMAS[ws.id] ?? []).find(
        (s) => s.name === params.schema,
      );
      if (!schema) return httpError(404, "Schema not found");
      const table = schema.tables.find((t) => t.name === params.table);
      if (!table) return httpError(404, "Table not found");
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
