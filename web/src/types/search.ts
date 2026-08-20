export interface SearchResult {
  type: "schema" | "table" | "saved_query";
  name: string;
  catalog?: string | null;
  schema_name?: string | null;
  id?: string | null;
  sql?: string | null;
  default_agent_id?: string | null;
}
