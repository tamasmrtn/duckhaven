import type { LineageGraph } from "@/types/lineage";

// A graph around `analytics.daily_active_users`, exercising every node kind the
// UI has to render: plain tables, an imported external source, and a node the
// viewer holds no grant on. Two providers agree on one edge, which is what the
// merged `providers` list is for.
function key(schema: string, table: string): string {
  return `cat:11111111-1111-1111-1111-111111111111/${schema}/${table}`;
}

const NOW = "2026-08-14T09:30:00Z";
const EARLIER = "2026-07-01T08:00:00Z";

export function makeLineage(): LineageGraph {
  return {
    root: key("analytics", "daily_active_users"),
    nodes: [
      {
        key: "ext:crm_pg/public/customers",
        kind: "external",
        catalog: null,
        schema_name: "public",
        table: "customers",
        system: "crm_pg",
        distance: -2,
      },
      {
        key: key("raw", "events"),
        kind: "table",
        catalog: "acme_analytics",
        schema_name: "raw",
        table: "events",
        system: null,
        distance: -1,
      },
      {
        key: "redacted:9f2c4a1b7e0d3856",
        kind: "redacted",
        catalog: null,
        schema_name: null,
        table: null,
        system: null,
        distance: -1,
      },
      {
        key: key("analytics", "daily_active_users"),
        kind: "table",
        catalog: "acme_analytics",
        schema_name: "analytics",
        table: "daily_active_users",
        system: null,
        distance: 0,
      },
      {
        key: key("analytics", "funnel"),
        kind: "table",
        catalog: "acme_analytics",
        schema_name: "analytics",
        table: "funnel",
        system: null,
        distance: 1,
      },
    ],
    edges: [
      {
        source_key: "ext:crm_pg/public/customers",
        target_key: key("raw", "events"),
        operation: "model",
        providers: ["dbt"],
        confidence: "exact",
        first_seen_at: EARLIER,
        last_seen_at: NOW,
        observation_count: 12,
        last_query_id: null,
        columns: [],
      },
      {
        source_key: key("raw", "events"),
        target_key: key("analytics", "daily_active_users"),
        operation: "create_table_as",
        providers: ["dbt", "execution"],
        confidence: "exact",
        first_seen_at: EARLIER,
        last_seen_at: NOW,
        observation_count: 47,
        last_query_id: "q-1",
        columns: [],
      },
      {
        source_key: "redacted:9f2c4a1b7e0d3856",
        target_key: key("analytics", "daily_active_users"),
        operation: "create_table_as",
        providers: ["execution"],
        confidence: "exact",
        first_seen_at: EARLIER,
        last_seen_at: NOW,
        observation_count: 47,
        last_query_id: "q-1",
        columns: [],
      },
      {
        source_key: key("analytics", "daily_active_users"),
        target_key: key("analytics", "funnel"),
        operation: "insert",
        providers: ["execution"],
        confidence: "exact",
        first_seen_at: EARLIER,
        last_seen_at: NOW,
        observation_count: 3,
        last_query_id: "q-2",
        columns: [],
      },
    ],
    truncated: false,
  };
}

// A table nothing has been built from and nothing reads: the empty state.
export function makeEmptyLineage(root: string): LineageGraph {
  return { root, nodes: [], edges: [], truncated: false };
}

export let LINEAGE = makeLineage();

export function resetLineage(): void {
  LINEAGE = makeLineage();
}
