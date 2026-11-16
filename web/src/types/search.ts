export type SearchResultType =
  | "catalog"
  | "schema"
  | "table"
  | "saved_query"
  | "dashboard"
  | "semantic_model";

export interface SearchResult {
  type: SearchResultType;
  /** For a dashboard its name and for a semantic model its slug: the key its page is addressed by. */
  name: string;
  /** The label to show for a dashboard or a semantic model. */
  title?: string | null;
  catalog?: string | null;
  schema_name?: string | null;
  id?: string | null;
  sql?: string | null;
  default_agent_id?: string | null;
}
