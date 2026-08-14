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

export interface LineageColumn {
  source_column: string;
  target_column: string;
}

export interface LineageEdge {
  source_key: string;
  target_key: string;
  operation: string | null;
  /** Every producer that asserted this relationship, sorted. */
  providers: string[];
  confidence: string;
  first_seen_at: string;
  last_seen_at: string;
  observation_count: number;
  last_query_id: string | null;
  /** Always empty today — column-level lineage is not derived yet. */
  columns: LineageColumn[];
}

export interface LineageGraph {
  root: string;
  nodes: LineageNode[];
  edges: LineageEdge[];
  /** A cap stopped the walk early, so this is a subset of the real graph. */
  truncated: boolean;
}

export type LineageDirection = "upstream" | "downstream" | "both";
