export type SearchResultType = "catalog" | "schema" | "table" | "saved_query";

export interface SearchResult {
  type: SearchResultType;
  name: string;
  catalog?: string | null;
  schema_name?: string | null;
  id?: string | null;
  sql?: string | null;
  default_agent_id?: string | null;
}
