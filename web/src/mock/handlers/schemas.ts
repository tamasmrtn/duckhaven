import { http, HttpResponse } from "msw";
import {
  SCHEMAS,
  generateSampleRows,
  generateSnapshots,
} from "../fixtures/schemas";
import { CATALOG_SCHEMAS, defaultCatalogSlug } from "../fixtures/catalogs";
import { findWorkspace } from "../fixtures/workspaces";
import { httpError } from "../lib/errors";
import type { CatalogSchema, CatalogTable, TableColumn } from "@/types/catalog";

// Resolve the catalog slug + its (mutable) schema store for a request. The
// legacy `/schemas` routes pass no catalog and target the workspace's default
// catalog (whose schemas live in SCHEMAS[wsId]); catalog-scoped routes pass the
// slug, and non-default catalogs read from CATALOG_SCHEMAS.
function store(
  wsId: string,
  catalogParam: string | undefined,
): { slug: string | undefined; list: CatalogSchema[] } {
  const def = defaultCatalogSlug(wsId);
  const slug = catalogParam ?? def;
  if (!slug) return { slug: undefined, list: [] };
  if (slug === def)
    return { slug, list: SCHEMAS[wsId] ?? (SCHEMAS[wsId] = []) };
  return { slug, list: CATALOG_SCHEMAS[slug] ?? (CATALOG_SCHEMAS[slug] = []) };
}

// Build the handler set for a path prefix. Registered twice: the legacy
// `/api/workspaces/:ws/schemas` and the canonical
// `/api/workspaces/:ws/catalogs/:catalog/schemas`.
function makeHandlers(prefix: string) {
  return [
    http.get(prefix, ({ params }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return httpError(404, "Workspace not found");
      const { slug, list } = store(ws.id, params.catalog as string | undefined);
      return HttpResponse.json(
        list.map((s) => ({ name: s.name, catalog: slug, workspace_id: ws.id })),
      );
    }),

    http.get(`${prefix}/:schema/tables`, ({ params }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return httpError(404, "Workspace not found");
      const { slug, list } = store(ws.id, params.catalog as string | undefined);
      const schema = list.find((s) => s.name === params.schema);
      if (!schema) return httpError(404, "Schema not found");
      return HttpResponse.json(
        schema.tables.map((t) => ({ ...t, catalog: slug })),
      );
    }),

    http.get(`${prefix}/:schema/tables/:table`, ({ params }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return httpError(404, "Workspace not found");
      const { slug, list } = store(ws.id, params.catalog as string | undefined);
      const schema = list.find((s) => s.name === params.schema);
      if (!schema) return httpError(404, "Schema not found");
      const table = schema.tables.find((t) => t.name === params.table);
      if (!table) return httpError(404, "Table not found");
      return HttpResponse.json({ ...table, catalog: slug });
    }),

    http.post(`${prefix}/refresh-stats`, ({ params }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return httpError(404, "Workspace not found");
      return HttpResponse.json({ probed: 0 });
    }),

    http.post(`${prefix}/:schema/tables/:table/recount`, ({ params }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return httpError(404, "Workspace not found");
      const { list } = store(ws.id, params.catalog as string | undefined);
      const schema = list.find((s) => s.name === params.schema);
      const table = schema?.tables.find((t) => t.name === params.table);
      if (!table) return httpError(404, "Table not found");
      return HttpResponse.json({ row_count: table.row_count });
    }),

    http.post(prefix, async ({ params, request }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return httpError(404, "Workspace not found");
      const { slug, list } = store(ws.id, params.catalog as string | undefined);
      const body = (await request.json()) as { name: string };
      if (list.some((s) => s.name === body.name)) {
        return httpError(409, "Schema exists");
      }
      list.push({
        name: body.name,
        catalog: slug,
        workspace_id: ws.id,
        tables: [],
      });
      return HttpResponse.json(
        { name: body.name, catalog: slug, catalog_name: slug },
        { status: 201 },
      );
    }),

    http.post(`${prefix}/:schema/tables`, async ({ params, request }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return httpError(404, "Workspace not found");
      const { slug, list } = store(ws.id, params.catalog as string | undefined);
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
        catalog: slug,
        workspace_id: ws.id,
        row_count: 0,
        size_bytes: 0,
        format: "Iceberg",
        catalog_commits: true,
        owner: "you",
        last_write_at: null,
        last_write_by: null,
        last_write_agent: null,
        format_version: 2,
        snapshot_id: null,
        snapshot_at: null,
        data_file_count: null,
        has_deletes: null,
        columns,
      };
      schema.tables.push(created);
      return HttpResponse.json(created, { status: 201 });
    }),

    http.delete(`${prefix}/:schema`, ({ params, request }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return httpError(404, "Workspace not found");
      const { list } = store(ws.id, params.catalog as string | undefined);
      const idx = list.findIndex((s) => s.name === params.schema);
      if (idx === -1) return httpError(404, "Schema not found");
      const cascade =
        new URL(request.url).searchParams.get("cascade") === "true";
      if (list[idx].tables.length > 0 && !cascade) {
        const names = list[idx].tables.map((t) => t.name).join(", ");
        return httpError(
          409,
          `Schema '${params.schema}' is not empty (tables: ${names}). Pass cascade=true to drop them too.`,
        );
      }
      list.splice(idx, 1);
      return new HttpResponse(null, { status: 204 });
    }),

    http.delete(`${prefix}/:schema/tables/:table`, ({ params }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return httpError(404, "Workspace not found");
      const { list } = store(ws.id, params.catalog as string | undefined);
      const schema = list.find((s) => s.name === params.schema);
      if (!schema) return httpError(404, "Schema not found");
      const idx = schema.tables.findIndex((t) => t.name === params.table);
      if (idx === -1) return httpError(404, "Table not found");
      schema.tables.splice(idx, 1);
      return new HttpResponse(null, { status: 204 });
    }),

    http.get(`${prefix}/:schema/tables/:table/sample`, ({ params }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return httpError(404, "Workspace not found");
      const { list } = store(ws.id, params.catalog as string | undefined);
      const schema = list.find((s) => s.name === params.schema);
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
    }),

    http.get(`${prefix}/:schema/tables/:table/snapshots`, ({ params }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return httpError(404, "Workspace not found");
      const { list } = store(ws.id, params.catalog as string | undefined);
      const schema = list.find((s) => s.name === params.schema);
      if (!schema) return httpError(404, "Schema not found");
      const table = schema.tables.find((t) => t.name === params.table);
      if (!table) return httpError(404, "Table not found");
      return HttpResponse.json(generateSnapshots(table));
    }),
  ];
}

export const schemaHandlers = [
  ...makeHandlers("/api/workspaces/:ws/catalogs/:catalog/schemas"),
  ...makeHandlers("/api/workspaces/:ws/schemas"),
];
