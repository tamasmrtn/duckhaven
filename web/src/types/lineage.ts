/** How much of a node the caller may see. `redacted` keeps the graph's shape
 *  without revealing a name the viewer holds no grant on. */
export type LineageNodeKind = "table" | "external" | "redacted";

export interface LineageNode {
  key: string;
  kind: LineageNodeKind;
  catalog: string | null;
  schema_name: string | null;
  table: string | null;
  /** The external system's name, for `external` nodes only. */
  system: string | null;
  /** Signed hops from the root: negative upstream, positive downstream. */
  distance: number;
}

/**
 * One `source column -> target column` relationship on an edge.
 *
 * Means the target column's values may be derived from the source column's — data
 * flow, not a mention. A column used only to filter rows, or only as a join key,
 * is deliberately absent.
 */
export interface LineageColumn {
  source_column: string;
  target_column: string;
  /** Which producers assert this mapping. api `LineageColumnOut.providers`. */
  providers: string[];
  stale: boolean;
}

/**
 * Whether anything worked out an edge's column detail.
 *
 * `derived` with no columns is a real answer — the source was read and none of
 * its values reached the target — which is exactly what the table graph cannot
 * express. `unsupported` means somebody tried and could not; `unknown` means
 * nobody tried. Never present the last two as "nothing flows".
 */
export type LineageColumnState = "unknown" | "derived" | "unsupported";

/** One producer's claim about a relationship, with its own freshness. Producers
 *  keep their own cadence, so an import that stopped running last quarter is
 *  stale even when a query confirmed the same pair this morning. */
export interface LineageProvider {
  name: string;
  first_seen_at: string;
  last_seen_at: string;
  observation_count: number;
  /** Nothing has re-asserted this producer's claim recently. */
  stale: boolean;
  /** Whether *this* producer worked out the column detail. */
  column_lineage: LineageColumnState;
}

export interface LineageEdge {
  source_key: string;
  target_key: string;
  operation: string | null;
  /** Every producer that asserted this relationship, sorted by name. */
  providers: LineageProvider[];
  confidence: string;
  /** Merged across providers: earliest, latest, and total. */
  first_seen_at: string;
  last_seen_at: string;
  observation_count: number;
  /** True only when every producer's claim is stale. */
  stale: boolean;
  last_query_id: string | null;
  /** Empty unless the request named one of this edge's endpoints in
   *  `columns_for`. Column detail scales with how wide the tables are, so it is
   *  fetched for the nodes somebody actually opened rather than for the graph. */
  columns: LineageColumn[];
  /** Makes an empty `columns` readable. See {@link LineageColumnState}. */
  column_lineage: LineageColumnState;
}

export interface LineageGraph {
  root: string;
  nodes: LineageNode[];
  edges: LineageEdge[];
  /** A cap stopped the walk early, so this is a subset of the real graph. */
  truncated: boolean;
  /** Part of the graph is outside this workspace and was dropped. Says only
   *  that something is missing — never what, where, or how much. */
  hidden: boolean;
  /** A cap stopped column detail short. The graph's shape is still complete;
   *  only the mappings inside it are partial. */
  columns_truncated: boolean;
}

export type LineageDirection = "upstream" | "downstream" | "both";
