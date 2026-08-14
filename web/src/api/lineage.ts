import { get } from "./client";
import type { LineageDirection, LineageGraph } from "@/types/lineage";

export interface LineageParams {
  direction?: LineageDirection;
  depth?: number;
  /** Restrict the graph to specific producers. Omit for all of them. */
  providers?: string[];
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
    const qs = query.toString();
    return get<LineageGraph>(
      `/workspaces/${ws}/catalogs/${catalog}/schemas/${schema}/tables/${table}/lineage${
        qs ? `?${qs}` : ""
      }`,
    );
  },
};
