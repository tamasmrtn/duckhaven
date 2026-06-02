import type { CatalogSchema } from "@/types/catalog";
import { seededCell } from "../lib/seed";

// Rich catalog shape is intentionally kept (the backend TableOut is under-built
// here and should be enriched to match — see plan §H). Schemas/tables are
// mutated by POST/DELETE handlers, so the store is rebuildable for isolation.
function makeSchemas(): Record<string, CatalogSchema[]> {
  return {
    "ws-1": [
      {
        name: "raw",
        workspace_id: "ws-1",
        tables: [
          {
            name: "events",
            schema_name: "raw",
            workspace_id: "ws-1",
            row_count: 42100000,
            size_bytes: 327155712,
            format: "Iceberg",
            catalog_commits: true,
            owner: "Marton",
            last_write_at: "2026-05-15T14:03:00Z",
            last_write_by: "jess@duckhaven.local",
            last_write_agent: "agent-b",
            columns: [
              { position: 1, name: "event_id", type: "UUID", nullable: false },
              { position: 2, name: "user_id", type: "UUID", nullable: true },
              {
                position: 3,
                name: "event_type",
                type: "VARCHAR",
                nullable: false,
              },
              {
                position: 4,
                name: "event_time",
                type: "TIMESTAMP",
                nullable: false,
              },
              { position: 5, name: "properties", type: "JSON", nullable: true },
            ],
          },
          {
            name: "users",
            schema_name: "raw",
            workspace_id: "ws-1",
            row_count: 1100000,
            size_bytes: 45678901,
            format: "Iceberg",
            catalog_commits: true,
            owner: "Marton",
            last_write_at: "2026-05-14T09:22:00Z",
            last_write_by: "marton@duckhaven.local",
            last_write_agent: "agent-a",
            columns: [
              { position: 1, name: "user_id", type: "UUID", nullable: false },
              { position: 2, name: "email", type: "VARCHAR", nullable: false },
              { position: 3, name: "name", type: "VARCHAR", nullable: true },
              {
                position: 4,
                name: "created_at",
                type: "TIMESTAMP",
                nullable: false,
              },
              { position: 5, name: "plan", type: "VARCHAR", nullable: true },
            ],
          },
          {
            name: "page_views",
            schema_name: "raw",
            workspace_id: "ws-1",
            row_count: 187000000,
            size_bytes: 2147483648,
            format: "Iceberg",
            catalog_commits: true,
            owner: "Jess",
            last_write_at: "2026-05-15T18:44:00Z",
            last_write_by: "jess@duckhaven.local",
            last_write_agent: "agent-b",
            columns: [
              { position: 1, name: "view_id", type: "UUID", nullable: false },
              { position: 2, name: "user_id", type: "UUID", nullable: true },
              {
                position: 3,
                name: "session_id",
                type: "VARCHAR",
                nullable: false,
              },
              { position: 4, name: "url", type: "VARCHAR", nullable: false },
              {
                position: 5,
                name: "referrer",
                type: "VARCHAR",
                nullable: true,
              },
              {
                position: 6,
                name: "viewed_at",
                type: "TIMESTAMP",
                nullable: false,
              },
            ],
          },
        ],
      },
      {
        name: "analytics",
        workspace_id: "ws-1",
        tables: [
          {
            name: "daily_active_users",
            schema_name: "analytics",
            workspace_id: "ws-1",
            row_count: 365,
            size_bytes: 12345,
            format: "Iceberg",
            catalog_commits: true,
            owner: "Marton",
            last_write_at: "2026-05-15T01:00:00Z",
            last_write_by: "marton@duckhaven.local",
            last_write_agent: "agent-a",
            columns: [
              { position: 1, name: "d", type: "DATE", nullable: false },
              { position: 2, name: "dau", type: "BIGINT", nullable: false },
              {
                position: 3,
                name: "new_users",
                type: "BIGINT",
                nullable: false,
              },
            ],
          },
          {
            name: "funnel",
            schema_name: "analytics",
            workspace_id: "ws-1",
            row_count: 12,
            size_bytes: 4096,
            format: "Iceberg",
            catalog_commits: true,
            owner: "Jess",
            last_write_at: "2026-05-12T15:00:00Z",
            last_write_by: "jess@duckhaven.local",
            last_write_agent: "agent-b",
            columns: [
              { position: 1, name: "step", type: "VARCHAR", nullable: false },
              { position: 2, name: "users", type: "BIGINT", nullable: false },
              { position: 3, name: "pct", type: "DOUBLE", nullable: false },
            ],
          },
        ],
      },
    ],
    "ws-2": [
      {
        name: "experiments",
        workspace_id: "ws-2",
        tables: [
          {
            name: "ab_assignments",
            schema_name: "experiments",
            workspace_id: "ws-2",
            row_count: 500000,
            size_bytes: 23456789,
            format: "Iceberg",
            catalog_commits: true,
            owner: "Jess",
            last_write_at: "2026-05-10T12:00:00Z",
            last_write_by: "jess@duckhaven.local",
            last_write_agent: "agent-a",
            columns: [
              { position: 1, name: "user_id", type: "UUID", nullable: false },
              {
                position: 2,
                name: "experiment_id",
                type: "VARCHAR",
                nullable: false,
              },
              {
                position: 3,
                name: "variant",
                type: "VARCHAR",
                nullable: false,
              },
              {
                position: 4,
                name: "assigned_at",
                type: "TIMESTAMP",
                nullable: false,
              },
            ],
          },
        ],
      },
    ],
    "ws-3": [
      {
        name: "shared",
        workspace_id: "ws-3",
        tables: [
          {
            name: "lookups",
            schema_name: "shared",
            workspace_id: "ws-3",
            row_count: 5000,
            size_bytes: 102400,
            format: "Iceberg",
            catalog_commits: true,
            owner: "Marton",
            last_write_at: "2026-04-01T00:00:00Z",
            last_write_by: "marton@duckhaven.local",
            last_write_agent: "agent-a",
            columns: [
              { position: 1, name: "key", type: "VARCHAR", nullable: false },
              { position: 2, name: "value", type: "VARCHAR", nullable: false },
              {
                position: 3,
                name: "category",
                type: "VARCHAR",
                nullable: true,
              },
            ],
          },
        ],
      },
    ],
    "ws-4": [],
  };
}

export let SCHEMAS = makeSchemas();

export function resetSchemas(): void {
  SCHEMAS = makeSchemas();
}

// Deterministic sample rows keyed by column type + row index (no randomness).
export function generateSampleRows(
  table: { columns: { name: string; type: string }[] },
  count = 10,
) {
  if (table.columns.length === 0) return [];
  return Array.from({ length: count }, (_, i) => {
    const row: Record<string, unknown> = {};
    for (const col of table.columns) {
      row[col.name] = seededCell(col, i);
    }
    return row;
  });
}
