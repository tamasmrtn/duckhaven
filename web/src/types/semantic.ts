/**
 * The semantic layer: curated definitions of what a workspace's business terms
 * mean, and how they are computed.
 *
 * Mirrors the server's `*Out` shapes field for field, in snake_case.
 */

export type ModelStatus = "draft" | "published" | "deprecated";

/**
 * Whether a definition's bindings still resolve against the live catalog.
 *
 * `unchecked` is not a softer `ok`: it means nothing has looked since something
 * changed. The compiler treats it as a reason to revalidate, and the UI shows it
 * differently from both of the other two.
 */
export type ValidationState = "ok" | "broken" | "unchecked";

export type DimensionKind = "categorical" | "time";

/**
 * Only ever a join toward a unique side. `one_to_many` is deliberately absent
 * everywhere in this system: joining that way multiplies fact rows and inflates
 * every metric that crosses it, with no error anywhere.
 */
export type Cardinality = "many_to_one" | "one_to_one";

export type Aggregation =
  "sum" | "count" | "count_distinct" | "avg" | "min" | "max";

export type TimeGrain = "day" | "week" | "month" | "quarter" | "year";

export type FilterOp =
  | "eq"
  | "ne"
  | "in"
  | "not_in"
  | "gt"
  | "gte"
  | "lt"
  | "lte"
  | "contains"
  | "is_null"
  | "is_not_null";

/**
 * A time window, stated explicitly. There is no default because "last month"
 * means the previous calendar month to one person, the trailing thirty days to
 * another, and month-to-date to a third.
 */
export type WindowKind = "last_complete" | "trailing" | "to_date" | "absolute";

export interface JoinColumn {
  left: string;
  right: string;
}

export interface SemanticDataset {
  id: string;
  name: string;
  description: string | null;
  synonyms: string[];
  catalog: string | null;
  schema_name: string;
  table_name: string;
  /** What makes this dataset safe to be the unique side of a join. */
  primary_key: string[];
  validation_state: ValidationState;
  validation_detail: string | null;
}

export interface SemanticDimension {
  id: string;
  name: string;
  dataset: string | null;
  display_name: string | null;
  description: string | null;
  synonyms: string[];
  kind: DimensionKind;
  expr: string;
  data_type: string | null;
  time_grains: string[];
  is_default_time: boolean;
  /**
   * A few real values, so a filter can be written against what is stored rather
   * than against what somebody said. "US" vs "United States" is otherwise a
   * silently empty result.
   */
  sample_values: string[];
  validation_state: ValidationState;
  validation_detail: string | null;
}

export interface SemanticMetric {
  id: string;
  name: string;
  dataset: string | null;
  display_name: string | null;
  description: string | null;
  synonyms: string[];
  agg: Aggregation;
  expr: string | null;
  filter: string | null;
  /**
   * Which timestamp this metric is measured on. The single most valuable field
   * here: "revenue last month" against `created_at` instead of `order_date`
   * returns a different number and no error.
   */
  time_dimension: string | null;
  caveat: string | null;
  status: ModelStatus;
  /** A readable rendering of the calculation, e.g. `SUM(total_amount) FILTER (…)`. */
  expression: string | null;
  validation_state: ValidationState;
  validation_detail: string | null;
}

export interface SemanticRelationship {
  id: string;
  name: string;
  left_dataset: string | null;
  right_dataset: string | null;
  join_columns: JoinColumn[];
  cardinality: Cardinality;
  validation_state: ValidationState;
  validation_detail: string | null;
}

export interface SemanticModelSummary {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  status: ModelStatus;
  /** `native` for anything authored here; an adapter name for anything imported. */
  provider: string;
  owner_id: string | null;
  metric_count: number;
  dimension_count: number;
  dataset_count: number;
  broken_count: number;
  created_at: string;
  updated_at: string;
}

export interface SemanticModel extends SemanticModelSummary {
  datasets: SemanticDataset[];
  dimensions: SemanticDimension[];
  metrics: SemanticMetric[];
  relationships: SemanticRelationship[];
}

export interface ValidationError {
  kind: string;
  name: string;
  detail: string;
}

export interface ValidationReport {
  ok: boolean;
  errors: ValidationError[];
  warnings: string[];
  checked_at: string | null;
}

export interface SemanticHit {
  kind: "metric" | "dimension";
  model: string;
  name: string;
  label: string;
  description: string | null;
  synonyms: string[];
  status: string;
  expression?: string | null;
  time_dimension?: string | null;
  caveat?: string | null;
  dimension_kind?: string | null;
  sample_values?: string[];
}

export interface SemanticSearchResult {
  hits: SemanticHit[];
  /**
   * Metrics tied at the top of the ranking — the ones a person has to choose
   * between. Populated when a term matches more than one authoritative
   * definition equally well.
   */
  ambiguous: SemanticHit[];
}

export interface TimeRangeInput {
  kind: WindowKind;
  grain?: TimeGrain;
  n?: number;
  start?: string;
  end?: string;
}

export interface DimensionFilterInput {
  dimension: string;
  op: FilterOp;
  values: (string | number | boolean | null)[];
}

export interface MetricQueryInput {
  model: string;
  metrics: string[];
  dimensions?: string[];
  grain?: TimeGrain;
  time_range?: TimeRangeInput;
  filters?: DimensionFilterInput[];
  order_by?: { field: string; descending: boolean }[];
  limit?: number;
}

export interface CompiledQuery {
  sql: string;
  definitions_used: Record<string, unknown>[];
  warnings: string[];
}

export interface SemanticDependent {
  kind: "metric" | "dimension";
  model: string;
  model_name: string;
  model_status: ModelStatus;
  name: string;
  label: string;
  status: string;
  dataset: string;
  columns: string[];
}

export interface TableSemantics {
  dependents: SemanticDependent[];
}

export interface SemanticSkipped {
  ref: string;
  reason: string;
  detail: string | null;
}

export interface SemanticImportResult {
  provider: string;
  run_id: string;
  created: number;
  updated: number;
  removed: number;
  skipped: SemanticSkipped[];
}

export interface ModelInput {
  slug: string;
  name: string;
  description?: string | null;
}

export interface DatasetInput {
  name: string;
  catalog: string;
  schema_name: string;
  table_name: string;
  description?: string | null;
  synonyms?: string[];
  primary_key?: string[];
}

export interface DimensionInput {
  name: string;
  dataset: string;
  kind?: DimensionKind;
  expr?: string | null;
  display_name?: string | null;
  description?: string | null;
  synonyms?: string[];
  time_grains?: TimeGrain[];
  is_default_time?: boolean;
  sample_values?: string[];
}

export interface MetricInput {
  name: string;
  dataset: string;
  agg: Aggregation;
  expr?: string | null;
  filter?: string | null;
  time_dimension?: string | null;
  display_name?: string | null;
  description?: string | null;
  synonyms?: string[];
  caveat?: string | null;
}

export interface MetricPatch {
  display_name?: string | null;
  description?: string | null;
  synonyms?: string[];
  agg?: Aggregation;
  expr?: string | null;
  filter?: string | null;
  time_dimension?: string | null;
  caveat?: string | null;
  status?: ModelStatus;
}

export interface RelationshipInput {
  name: string;
  left_dataset: string;
  right_dataset: string;
  join_columns: JoinColumn[];
  cardinality?: Cardinality;
}
