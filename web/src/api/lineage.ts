import { get } from "./client";
import type { LineageDirection, LineageGraph } from "@/types/lineage";

export interface LineageParams {
  direction?: LineageDirection;
  depth?: number;
  /** Restrict the graph to specific producers. Omit for all of them. */
  providers?: string[];
  /**
   * Node keys whose column-level detail should come back attached.
   *
   * Empty by default, and deliberately not "all". A graph is bounded by node
   * count, but its column detail is bounded by how wide those tables are — so
   * asking for the nodes somebody has actually opened keeps the response
   * proportional to what is on screen.
   */
  columnsFor?: string[];
}

export const lineageApi = {
  tableLineage: (
    ws: string,
    catalog: string,
    schema: string,
    table: string,
    params: LineageParams = {},
  ) => {
    const query = new URLSearchParams();
    if (params.direction) query.set("direction", params.direction);
    if (params.depth != null) query.set("depth", String(params.depth));
    for (const provider of params.providers ?? [])
      query.append("provider", provider);
    for (const key of params.columnsFor ?? []) query.append("columns_for", key);
    const qs = query.toString();
    return get<LineageGraph>(
      `/workspaces/${ws}/catalogs/${catalog}/schemas/${schema}/tables/${table}/lineage${
        qs ? `?${qs}` : ""
      }`,
    );
  },
};
