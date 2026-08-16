import type { LineageGraph } from "@/types/lineage";

// A graph around `analytics.daily_active_users`, exercising every node kind the
// UI has to render: plain tables, an imported external source, and a node the
// viewer holds no grant on. Two providers agree on one edge, which is what the
// merged `providers` list is for — and on that edge the imported claim has gone
// stale while the execution-derived one has not, so the per-provider freshness
// markers are reachable in dev without a handler override.
function key(schema: string, table: string): string {
  return `cat:11111111-1111-1111-1111-111111111111/${schema}/${table}`;
}

const NOW = "2026-08-14T09:30:00Z";
const EARLIER = "2026-07-01T08:00:00Z";
// Comfortably past the default 30-day staleness window.
const LONG_AGO = "2026-02-11T08:00:00Z";

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
        // Nothing has re-imported this in months: stale, and the only producer
        // asserting it, so the edge itself is stale.
        source_key: "ext:crm_pg/public/customers",
        target_key: key("raw", "events"),
        operation: "model",
        providers: [
          {
            name: "dbt",
            first_seen_at: EARLIER,
            last_seen_at: LONG_AGO,
            observation_count: 12,
            stale: true,
            column_lineage: "unsupported",
          },
        ],
        confidence: "exact",
        first_seen_at: EARLIER,
        last_seen_at: LONG_AGO,
        observation_count: 12,
        stale: true,
        last_query_id: null,
        // An external source whose columns cannot be tied to an asset: data does
        // flow, we just cannot say which columns, and that is not the same as
        // saying none do.
        columns: [],
        column_lineage: "unsupported",
      },
      {
        // Two producers, one of which stopped. The edge stays current because
        // the other still confirms it.
        source_key: key("raw", "events"),
        target_key: key("analytics", "daily_active_users"),
        operation: "create_table_as",
        providers: [
          {
            name: "dbt",
            first_seen_at: EARLIER,
            last_seen_at: LONG_AGO,
            observation_count: 12,
            stale: true,
            column_lineage: "derived",
          },
          {
            name: "execution",
            first_seen_at: EARLIER,
            last_seen_at: NOW,
            observation_count: 35,
            stale: false,
            column_lineage: "derived",
          },
        ],
        confidence: "exact",
        first_seen_at: EARLIER,
        last_seen_at: NOW,
        observation_count: 47,
        stale: false,
        last_query_id: "q-1",
        // Two upstream columns feeding one output, and one feeding two — both
        // shapes the model has to support — plus a mapping that has gone stale
        // on its own while the rest of the edge stayed current.
        columns: [
          {
            source_column: "user_id",
            target_column: "user_id",
            providers: ["dbt", "execution"],
            stale: false,
          },
          {
            source_column: "event_id",
            target_column: "active_count",
            providers: ["execution"],
            stale: false,
          },
          {
            source_column: "session_id",
            target_column: "active_count",
            providers: ["execution"],
            stale: false,
          },
          {
            source_column: "occurred_at",
            target_column: "day",
            providers: ["dbt", "execution"],
            stale: true,
          },
        ],
        column_lineage: "derived",
      },
      {
        source_key: "redacted:9f2c4a1b7e0d3856",
        target_key: key("analytics", "daily_active_users"),
        operation: "create_table_as",
        providers: [
          {
            name: "execution",
            first_seen_at: EARLIER,
            last_seen_at: NOW,
            observation_count: 47,
            stale: false,
            column_lineage: "derived",
          },
        ],
        confidence: "exact",
        first_seen_at: EARLIER,
        last_seen_at: NOW,
        observation_count: 47,
        stale: false,
        last_query_id: "q-1",
        // Withheld: naming a restricted table's columns would give away more
        // than the table name the redaction already holds back.
        columns: [],
        column_lineage: "derived",
      },
      {
        source_key: key("analytics", "daily_active_users"),
        target_key: key("analytics", "funnel"),
        operation: "insert",
        providers: [
          {
            name: "execution",
            first_seen_at: EARLIER,
            last_seen_at: NOW,
            observation_count: 3,
            stale: false,
            column_lineage: "derived",
          },
        ],
        confidence: "exact",
        first_seen_at: EARLIER,
        last_seen_at: NOW,
        observation_count: 3,
        stale: false,
        last_query_id: "q-2",
        // Read and filtered on, but none of its values reach the target. The
        // finding the table graph could never state.
        columns: [],
        column_lineage: "derived",
      },
    ],
    truncated: false,
    hidden: false,
    columns_truncated: false,
  };
}

// A table nothing has been built from and nothing reads: the empty state.
export function makeEmptyLineage(root: string): LineageGraph {
  return {
    root,
    nodes: [],
    edges: [],
    truncated: false,
    hidden: false,
    columns_truncated: false,
  };
}

// A table that *does* have lineage, all of it in catalogs this workspace cannot
// see. Indistinguishable from the empty graph without the `hidden` flag, which
// is the entire reason the flag exists.
export function makeHiddenLineage(root: string): LineageGraph {
  return {
    root,
    nodes: [],
    edges: [],
    truncated: false,
    hidden: true,
    columns_truncated: false,
  };
}

export let LINEAGE = makeLineage();

export function resetLineage(): void {
  LINEAGE = makeLineage();
}
