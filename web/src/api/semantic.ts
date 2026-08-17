import { del, get, patch, post, postText } from "./client";
import type {
  CompiledQuery,
  DatasetInput,
  DimensionInput,
  MetricInput,
  MetricPatch,
  MetricQueryInput,
  ModelInput,
  RelationshipInput,
  SemanticDataset,
  SemanticDimension,
  SemanticImportResult,
  SemanticMetric,
  SemanticModel,
  SemanticModelSummary,
  SemanticRelationship,
  SemanticSearchResult,
  TableSemantics,
  ValidationReport,
} from "@/types/semantic";

const base = (ws: string) => `/workspaces/${ws}/semantic`;

export const semanticApi = {
  listModels: (ws: string, status?: string) => {
    const qs = status ? `?status=${encodeURIComponent(status)}` : "";
    return get<SemanticModelSummary[]>(`${base(ws)}/models${qs}`);
  },

  getModel: (ws: string, slug: string) =>
    get<SemanticModel>(`${base(ws)}/models/${slug}`),

  createModel: (ws: string, body: ModelInput) =>
    post<SemanticModel>(`${base(ws)}/models`, body),

  updateModel: (ws: string, slug: string, body: Partial<ModelInput>) =>
    patch<SemanticModel>(`${base(ws)}/models/${slug}`, body),

  deleteModel: (ws: string, slug: string) => del(`${base(ws)}/models/${slug}`),

  publishModel: (ws: string, slug: string) =>
    post<SemanticModel>(`${base(ws)}/models/${slug}/publish`),

  deprecateModel: (ws: string, slug: string) =>
    post<SemanticModel>(`${base(ws)}/models/${slug}/deprecate`),

  validateModel: (ws: string, slug: string) =>
    post<ValidationReport>(`${base(ws)}/models/${slug}/validate`),

  addDataset: (ws: string, slug: string, body: DatasetInput) =>
    post<SemanticDataset>(`${base(ws)}/models/${slug}/datasets`, body),

  addDimension: (ws: string, slug: string, body: DimensionInput) =>
    post<SemanticDimension>(`${base(ws)}/models/${slug}/dimensions`, body),

  addMetric: (ws: string, slug: string, body: MetricInput) =>
    post<SemanticMetric>(`${base(ws)}/models/${slug}/metrics`, body),

  updateMetric: (ws: string, slug: string, name: string, body: MetricPatch) =>
    patch<SemanticMetric>(`${base(ws)}/models/${slug}/metrics/${name}`, body),

  addRelationship: (ws: string, slug: string, body: RelationshipInput) =>
    post<SemanticRelationship>(
      `${base(ws)}/models/${slug}/relationships`,
      body,
    ),

  /** Which dimensions a metric can legally be sliced by — a lookup, not a guess. */
  metricDimensions: (ws: string, slug: string, name: string) =>
    get<string[]>(`${base(ws)}/models/${slug}/metrics/${name}/dimensions`),

  search: (ws: string, q: string, publishedOnly = true) => {
    const query = new URLSearchParams({
      q,
      published_only: String(publishedOnly),
    });
    return get<SemanticSearchResult>(`${base(ws)}/search?${query}`);
  },

  /**
   * Compile a metric request to SQL. Deliberately does not execute: the caller
   * submits the SQL through the ordinary query path, so there is exactly one
   * execution route and one grant check.
   */
  compile: (ws: string, body: MetricQueryInput, publishedOnly = true) =>
    post<CompiledQuery>(
      `${base(ws)}/compile?published_only=${publishedOnly}`,
      body,
    ),

  /** Which semantic definitions depend on one physical table. */
  tableSemantics: (
    ws: string,
    catalog: string,
    schema: string,
    table: string,
    column?: string,
  ) => {
    const qs = column ? `?column=${encodeURIComponent(column)}` : "";
    return get<TableSemantics>(
      `/workspaces/${ws}/catalogs/${catalog}/schemas/${schema}/tables/${table}/semantic${qs}`,
    );
  },

  importDocument: (
    ws: string,
    provider: string,
    document: string,
    reconcile: "none" | "provider_run" = "provider_run",
  ) =>
    postText<SemanticImportResult>(
      `${base(ws)}/imports/${provider}?reconcile=${reconcile}`,
      document,
    ),
};
