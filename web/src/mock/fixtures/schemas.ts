import type { CatalogSchema, TableSnapshot } from "@/types/catalog";
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
            row_count_estimate: 42100000,
            size_bytes: 327155712,
            format: "Iceberg",
            catalog_commits: true,
            owner: "Marton",
            last_write_at: "2026-05-15T14:03:00Z",
            last_write_by: "jess@duckhaven.local",
            last_write_agent: "agent-b",
            format_version: 2,
            snapshot_id: "7264354987654321234",
            snapshot_at: "2026-05-15T14:03:00Z",
            data_file_count: 128,
            has_deletes: true,
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
            row_count_estimate: 1100000,
            size_bytes: 45678901,
            format: "Iceberg",
            catalog_commits: true,
            owner: "Marton",
            last_write_at: "2026-05-14T09:22:00Z",
            last_write_by: "marton@duckhaven.local",
            last_write_agent: "agent-a",
            format_version: 2,
            snapshot_id: null,
            snapshot_at: null,
            data_file_count: null,
            has_deletes: null,
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
            row_count_estimate: 187000000,
            size_bytes: 2147483648,
            format: "Iceberg",
            catalog_commits: true,
            owner: "Jess",
            last_write_at: "2026-05-15T18:44:00Z",
            last_write_by: "jess@duckhaven.local",
            last_write_agent: "agent-b",
            format_version: 2,
            snapshot_id: null,
            snapshot_at: null,
            data_file_count: null,
            has_deletes: null,
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
            row_count_estimate: 365,
            size_bytes: 12345,
            format: "Iceberg",
            catalog_commits: true,
            owner: "Marton",
            last_write_at: "2026-05-15T01:00:00Z",
            last_write_by: "marton@duckhaven.local",
            last_write_agent: "agent-a",
            format_version: 2,
            snapshot_id: null,
            snapshot_at: null,
            data_file_count: null,
            has_deletes: null,
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
            row_count_estimate: 12,
            size_bytes: 4096,
            format: "Iceberg",
            catalog_commits: true,
            owner: "Jess",
            last_write_at: "2026-05-12T15:00:00Z",
            last_write_by: "jess@duckhaven.local",
            last_write_agent: "agent-b",
            format_version: 2,
            snapshot_id: null,
            snapshot_at: null,
            data_file_count: null,
            has_deletes: null,
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
            row_count_estimate: 500000,
            size_bytes: 23456789,
            format: "Iceberg",
            catalog_commits: true,
            owner: "Jess",
            last_write_at: "2026-05-10T12:00:00Z",
            last_write_by: "jess@duckhaven.local",
            last_write_agent: "agent-a",
            format_version: 2,
            snapshot_id: null,
            snapshot_at: null,
            data_file_count: null,
            has_deletes: null,
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
            row_count_estimate: 5000,
            size_bytes: 102400,
            format: "Iceberg",
            catalog_commits: true,
            owner: "Marton",
            last_write_at: "2026-04-01T00:00:00Z",
            last_write_by: "marton@duckhaven.local",
            last_write_agent: "agent-a",
            format_version: 2,
            snapshot_id: null,
            snapshot_at: null,
            data_file_count: null,
            has_deletes: null,
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

// Deterministic snapshot history for a table. A table with no `snapshot_id`
// has never been written to → empty history (drives the empty-state UI). One
// with a current snapshot gets a short, newest-first log with change metrics.
export function generateSnapshots(table: {
  snapshot_id: string | null;
  snapshot_at: string | null;
  row_count: number | null;
}): TableSnapshot[] {
  if (!table.snapshot_id) return [];
  const head = table.snapshot_id;
  const headMs = table.snapshot_at
    ? Date.parse(table.snapshot_at)
    : Date.parse("2026-05-15T14:03:00Z");
  const hour = 3600_000;
  const total = table.row_count ?? 1000;
  // Newest first: overwrite (current) ← append ← append (initial create).
  return [
    {
      snapshot_id: head,
      parent_snapshot_id: `${head}1`,
      committed_at: new Date(headMs).toISOString(),
      operation: "overwrite",
      is_current: true,
      schema_id: 0,
      added_records: Math.round(total * 0.1),
      deleted_records: Math.round(total * 0.05),
      total_records: total,
      added_data_files: 4,
      total_data_files: 128,
    },
    {
      snapshot_id: `${head}1`,
      parent_snapshot_id: `${head}2`,
      committed_at: new Date(headMs - hour).toISOString(),
      operation: "append",
      is_current: false,
      schema_id: 0,
      added_records: Math.round(total * 0.3),
      deleted_records: null,
      total_records: Math.round(total * 0.95),
      added_data_files: 12,
      total_data_files: 124,
    },
    {
      snapshot_id: `${head}2`,
      parent_snapshot_id: null,
      committed_at: new Date(headMs - 24 * hour).toISOString(),
      operation: "append",
      is_current: false,
      schema_id: 0,
      added_records: Math.round(total * 0.65),
      deleted_records: null,
      total_records: Math.round(total * 0.65),
      added_data_files: 100,
      total_data_files: 100,
    },
  ];
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
