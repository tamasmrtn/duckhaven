import { get } from "./client";
import type { SearchResult, SearchResultType } from "@/types/search";

/** A search report: truncated by `limit`, with no cursor to walk. */
export interface SearchResults {
  items: SearchResult[];
  has_more: boolean;
}

export interface SearchOptions {
  // Only these kinds of object; every kind when omitted.
  types?: SearchResultType[];
  limit?: number;
}

function searchPath(ws: string, q: string, opts: SearchOptions): string {
  const params = new URLSearchParams({ q });
  for (const t of opts.types ?? []) params.append("types", t);
  if (opts.limit != null) params.set("limit", String(opts.limit));
  return `/workspaces/${ws}/search?${params}`;
}

export const searchApi = {
  search: (ws: string, q: string) =>
    get<SearchResults>(searchPath(ws, q, {})).then((r) => r.items),

  // The full report, `has_more` included, for callers that say when it was cut.
  report: (ws: string, q: string, opts: SearchOptions) =>
    get<SearchResults>(searchPath(ws, q, opts)),
};
