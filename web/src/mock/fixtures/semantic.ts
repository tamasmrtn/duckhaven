import type {
  SemanticModel,
  SemanticModelSummary,
  ValidationReport,
} from "@/types/semantic";

/**
 * One published model over the same `raw.events` / `raw.users` tables the catalog
 * fixtures already define, plus a draft that is deliberately broken.
 *
 * Bound to real fixture tables rather than invented ones so the mock world stays
 * internally consistent: opening `raw.events` in the catalog and asking what
 * depends on it gives the same answer the semantic pages do.
 *
 * The broken draft is not padding. "A definition whose column was dropped" is a
 * state the UI has to render distinctly from both healthy and unsaved, and it is
 * the one a screenshot would otherwise never show.
 */
const SALES: SemanticModel = {
  id: "sem-model-1",
  slug: "sales",
  name: "Sales",
  description: "Product events, revenue, and the users behind them.",
  status: "published",
  provider: "native",
  owner_id: "user-1",
  metric_count: 3,
  dimension_count: 3,
  dataset_count: 2,
  broken_count: 0,
  created_at: "2026-08-01T09:00:00Z",
  updated_at: "2026-08-14T16:20:00Z",
  datasets: [
    {
      id: "sem-ds-1",
      name: "events",
      description: "One row per product event.",
      synonyms: [],
      catalog: "acme_analytics",
      schema_name: "raw",
      table_name: "events",
      primary_key: ["event_id"],
      validation_state: "ok",
      validation_detail: null,
    },
    {
      id: "sem-ds-2",
      name: "users",
      description: "One row per signed-up user.",
      synonyms: ["customers", "accounts"],
      catalog: "acme_analytics",
      schema_name: "raw",
      table_name: "users",
      primary_key: ["user_id"],
      validation_state: "ok",
      validation_detail: null,
    },
  ],
  dimensions: [
    {
      id: "sem-dim-1",
      name: "event_time",
      dataset: "events",
      display_name: "Event time",
      description: "When the event happened.",
      synonyms: [],
      kind: "time",
      expr: "event_time",
      data_type: "timestamp",
      time_grains: ["day", "week", "month", "quarter", "year"],
      is_default_time: true,
      sample_values: [],
      validation_state: "ok",
      validation_detail: null,
    },
    {
      id: "sem-dim-2",
      name: "plan",
      dataset: "users",
      display_name: "Plan",
      description: "The user's current subscription plan.",
      synonyms: ["tier", "package"],
      kind: "categorical",
      expr: "plan",
      data_type: "varchar",
      time_grains: [],
      is_default_time: false,
      sample_values: ["free", "pro", "enterprise"],
      validation_state: "ok",
      validation_detail: null,
    },
    {
      id: "sem-dim-3",
      name: "event_type",
      dataset: "events",
      display_name: "Event type",
      description: null,
      synonyms: ["action"],
      kind: "categorical",
      expr: "event_type",
      data_type: "varchar",
      time_grains: [],
      is_default_time: false,
      sample_values: ["signup", "purchase"],
      validation_state: "ok",
      validation_detail: null,
    },
  ],
  metrics: [
    {
      id: "sem-met-1",
      name: "revenue",
      dataset: "events",
      display_name: "Revenue",
      description: "Net booked revenue from purchase events.",
      synonyms: ["turnover", "gmv"],
      agg: "sum",
      expr: "amount",
      filter: "event_type = 'purchase'",
      time_dimension: "event_time",
      caveat: "Excludes internal test accounts.",
      status: "published",
      expression: "SUM(amount) FILTER (WHERE event_type = 'purchase')",
      validation_state: "ok",
      validation_detail: null,
    },
    {
      id: "sem-met-2",
      name: "event_count",
      dataset: "events",
      display_name: "Events",
      description: "How many events were recorded.",
      synonyms: ["events"],
      agg: "count",
      expr: null,
      filter: null,
      time_dimension: "event_time",
      caveat: null,
      status: "published",
      expression: "COUNT(*)",
      validation_state: "ok",
      validation_detail: null,
    },
    {
      id: "sem-met-3",
      name: "active_users",
      dataset: "events",
      display_name: "Active users",
      description: "Distinct users with at least one event in the period.",
      synonyms: ["active customers"],
      agg: "count_distinct",
      expr: "user_id",
      filter: null,
      time_dimension: "event_time",
      caveat: null,
      status: "published",
      expression: "COUNT(DISTINCT user_id)",
      validation_state: "ok",
      validation_detail: null,
    },
  ],
  relationships: [
    {
      id: "sem-rel-1",
      name: "events_to_users",
      left_dataset: "events",
      right_dataset: "users",
      join_columns: [{ left: "user_id", right: "user_id" }],
      cardinality: "many_to_one",
      validation_state: "ok",
      validation_detail: null,
    },
  ],
};

const MARKETING: SemanticModel = {
  id: "sem-model-2",
  slug: "marketing",
  name: "Marketing",
  description: "Funnel conversion and spend. Imported from dbt.",
  status: "draft",
  provider: "dbt",
  owner_id: "user-1",
  metric_count: 1,
  dimension_count: 1,
  dataset_count: 1,
  broken_count: 1,
  created_at: "2026-08-10T11:00:00Z",
  updated_at: "2026-08-16T08:00:00Z",
  datasets: [
    {
      id: "sem-ds-3",
      name: "funnel",
      description: null,
      synonyms: [],
      catalog: "acme_analytics",
      schema_name: "analytics",
      table_name: "funnel",
      primary_key: ["step"],
      validation_state: "ok",
      validation_detail: null,
    },
  ],
  dimensions: [
    {
      id: "sem-dim-4",
      name: "step",
      dataset: "funnel",
      display_name: "Step",
      description: null,
      synonyms: [],
      kind: "categorical",
      expr: "step",
      data_type: "varchar",
      time_grains: [],
      is_default_time: false,
      sample_values: ["visit", "signup"],
      validation_state: "ok",
      validation_detail: null,
    },
  ],
  metrics: [
    {
      id: "sem-met-4",
      name: "spend",
      dataset: "funnel",
      display_name: "Spend",
      description: "Total campaign spend.",
      synonyms: [],
      agg: "sum",
      expr: "cost_usd",
      filter: null,
      time_dimension: null,
      caveat: null,
      status: "draft",
      expression: "SUM(cost_usd)",
      validation_state: "broken",
      validation_detail:
        "Metric 'spend' expression references column(s) that no longer exist: cost_usd.",
    },
  ],
  relationships: [],
};

const INITIAL: SemanticModel[] = [SALES, MARKETING];

export let SEMANTIC_MODELS: SemanticModel[] = structuredClone(INITIAL);

export function resetSemantic(): void {
  SEMANTIC_MODELS = structuredClone(INITIAL);
}

export function summarize(model: SemanticModel): SemanticModelSummary {
  const {
    datasets: _datasets,
    dimensions: _dimensions,
    metrics: _metrics,
    relationships: _relationships,
    ...summary
  } = model;
  return summary;
}

export function reportFor(model: SemanticModel): ValidationReport {
  const errors = [
    ...model.datasets,
    ...model.dimensions,
    ...model.metrics,
    ...model.relationships,
  ]
    .filter((item) => item.validation_state === "broken")
    .map((item) => ({
      kind: "definition",
      name: item.name,
      detail: item.validation_detail ?? "This definition no longer resolves.",
    }));
  return {
    ok: errors.length === 0,
    errors,
    warnings: [],
    checked_at: "2026-08-17T09:00:00Z",
  };
}
