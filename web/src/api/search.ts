import { get } from "./client";
import type { SearchResult } from "@/types/search";

/** A search report: truncated by `limit`, with no cursor to walk. */
interface SearchResults {
  items: SearchResult[];
  has_more: boolean;
}

export const searchApi = {
  search: (ws: string, q: string) =>
    get<SearchResults>(
      `/workspaces/${ws}/search?q=${encodeURIComponent(q)}`,
    ).then((r) => r.items),
};
