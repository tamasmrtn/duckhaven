import { get } from "./client";
import type { SearchResult } from "@/types/search";

export const searchApi = {
  search: (ws: string, q: string) =>
    get<SearchResult[]>(`/workspaces/${ws}/search?q=${encodeURIComponent(q)}`),
};
