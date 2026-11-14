import type { MetricFormat, Polarity } from "./semantic";

// Mirrors api/schemas/dashboard.py and the events of
// api/services/dashboards/run.py. A dashboard file is described here only as
// far as the page reads it: its layout, titles and parameters. What a widget
// computes arrives in its run event, worked out by the server.

export type DashboardStatus = "draft" | "published";
export type DashboardSource = "ui" | "git";

export interface DashboardSummary {
  id: string;
  name: string;
  title: string;
  description: string | null;
  labels: string[];
  /** `draft` until the first publish. */
  status: DashboardStatus;
  /** Whether there are unpublished changes. */
  has_draft: boolean;
  source: DashboardSource;
  source_id: string | null;
  /** Managed in git: changed only by applying its file. */
  managed: boolean;
  version: number;
  created_at: string;
  updated_at: string;
  updated_by: string | null;
  updated_via: "ui" | "rest" | "cli" | "assistant" | "mcp";
  published_at: string | null;
}

export type WidgetType = "kpi" | "line" | "bar" | "table" | "text";

/** One widget of a file, as far as the page lays it out. */
export interface WidgetSpec {
  id: string;
  type: WidgetType;
  title?: string | null;
  description?: string | null;
  /** Columns of the 12-column row; omitted widths share what is left. */
  width?: number | null;
  /** A text widget's content. */
  markdown?: string;
  /** A metric widget's query, as far as the page reads it. */
  query?: { model: string; dimensions?: string[] } | null;
}

export interface RowSpec {
  widgets: WidgetSpec[];
}

export interface TimeRangeParameterSpec {
  type: "time_range";
  label?: string | null;
  default?: unknown;
}

export interface DimensionParameterSpec {
  type: "dimension";
  label?: string | null;
  model: string;
  dimension: string;
  default?: string | number | boolean | null;
}

export type ParameterSpec = TimeRangeParameterSpec | DimensionParameterSpec;

export interface DashboardFile {
  version: number;
  name: string;
  title: string;
  description?: string | null;
  labels?: string[];
  parameters?: Record<string, ParameterSpec>;
  rows: RowSpec[];
}

export interface Diagnostic {
  path: string;
  line: number | null;
  col: number | null;
  message: string;
  hint?: string | null;
}

export interface DashboardRef {
  kind: "model" | "metric" | "dimension" | "table";
  model: string | null;
  name: string;
}

export interface Dashboard extends DashboardSummary {
  /** Whether `spec` is the draft rather than the published file. */
  draft: boolean;
  spec: DashboardFile;
  source_text: string | null;
  spec_hash: string;
  spec_version: number;
  source_ref: string | null;
  client: string | null;
  created_by: string | null;
  published_by: string | null;
  refs: DashboardRef[];
  warnings: Diagnostic[];
}

// ── Editing ──────────────────────────────────────────────────────────────────

/** One publish, as the revision list shows it. */
export interface DashboardRevisionSummary {
  number: number;
  spec_hash: string;
  message: string | null;
  published_by: string | null;
  published_at: string;
}

export interface DashboardRevision extends DashboardRevisionSummary {
  spec: DashboardFile;
  source_text: string | null;
}

/** A file held on the server to try out, without saving it as a dashboard. */
export interface DashboardPreview {
  id: string;
  spec: DashboardFile;
  source_text: string | null;
  spec_hash: string;
  created_at: string;
  updated_at: string;
  expires_at: string;
  warnings: Diagnostic[];
}

/** A file checked the way publishing checks it. */
export interface DashboardValidation {
  valid: boolean;
  errors: Diagnostic[];
  warnings: Diagnostic[];
  refs: DashboardRef[];
}

// ── A run ────────────────────────────────────────────────────────────────────

/** What a request asks a run for. Every field may be left out. */
export interface DashboardRunRequest {
  params?: Record<string, unknown>;
  widgets?: string[];
  use_cache?: boolean;
}

/** A resolved window. `end` is the first day not included. */
export interface RunWindow {
  start: string | null;
  end: string | null;
  text: string;
}

export interface RunStart {
  type: "start";
  run_id: string;
  dashboard: {
    name: string;
    version: number | null;
    spec_hash: string;
    draft: boolean;
    preview: string | null;
  };
  today: string;
  timezone: string;
  parameters: {
    name: string;
    type: "time_range" | "dimension";
    value: unknown;
    window: RunWindow | null;
  }[];
  /** The widgets this run will answer, in file order. */
  widgets: string[];
}

export interface RunCompute {
  type: "compute";
  state: "starting";
  /** The widgets waiting for compute to start. */
  widgets: string[];
}

export type SkipReason =
  "ignored" | "own_dimension" | "other_model" | "unreachable" | "not_bound";

export interface WidgetScope {
  window: RunWindow | null;
  compare_window: RunWindow | null;
  applied: string[];
  skipped: { parameter: string; reason: SkipReason }[];
}

export type ColumnRole = "time" | "dimension" | "measure";

export interface RunColumn {
  name: string;
  /** The DuckDB type, when the agent reported one. */
  type: string | null;
  role: ColumnRole | null;
  label?: string;
  format?: MetricFormat | null;
  unit?: string | null;
  currency?: string | null;
}

/** A cell as JSON carries it. DECIMAL and wide integers arrive as strings. */
export type Cell = string | number | boolean | null;

export interface ChartMeasure {
  field: string;
  label: string;
  format?: MetricFormat | null;
  unit?: string | null;
  currency?: string | null;
  polarity?: Polarity | null;
}

/** What to draw, decided by the server and naming no chart library. */
export interface ChartSpec {
  type: "kpi" | "line" | "bar" | "table";
  x: { field: string; kind: "time" | "category"; grain?: string | null } | null;
  y: ChartMeasure[];
  series: { field: string; keys: string[] } | null;
  stack: boolean;
  orientation: "vertical" | "horizontal" | null;
  /** Series key to colour slot, 0 to 7. */
  colors: Record<string, number>;
  limits: { rows: number | null; series: number };
}

export interface WidgetCompare {
  current: Cell;
  previous: Cell;
  delta: Cell;
  delta_pct: number | null;
  /** Whether the change is good news; null when neutral or nothing changed. */
  good: boolean | null;
  window_text: string | null;
}

/** A metric or dimension a widget's query read, as the semantic layer defines it. */
export interface WidgetDefinition {
  kind: "metric" | "dimension" | "time_range";
  model: string;
  name: string;
  label?: string;
  description?: string | null;
  expression?: string | null;
  caveat?: string | null;
}

export type WidgetState = "ok" | "empty" | "error" | "no_access";

export interface RunWidget {
  type: "widget";
  id: string;
  state: WidgetState;
  scope: WidgetScope;
  /** Why an `error` or `no_access` widget has no rows. */
  error?: string | null;
  rows?: {
    columns: RunColumn[];
    data: Cell[][];
    /** The query returned more rows than the widget reads. */
    truncated: boolean;
    query_id: string;
  };
  chart?: ChartSpec;
  compare?: WidgetCompare;
  definitions?: WidgetDefinition[];
  cache?: "hit" | "miss" | "bypass" | "ineligible" | null;
  sql?: string;
  warnings?: string[];
  timing?: { elapsed_ms: number; query_ms?: number };
}

export interface RunDone {
  type: "done";
  elapsed_ms: number;
  widgets: Partial<Record<WidgetState, number>>;
  statements: number;
  cache_hits: number;
  batch: { used: boolean; batches: number };
}

export interface RunError {
  type: "error";
  message: string;
}

export type RunEvent = RunStart | RunCompute | RunWidget | RunDone | RunError;
