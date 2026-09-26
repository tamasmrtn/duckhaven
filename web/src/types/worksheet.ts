// A user's private, autosaved SQL editor tab (api/schemas/worksheet.py).
export interface Worksheet {
  id: string;
  workspace_id: string;
  owner_id: string;
  title: string;
  sql: string;
  agent_id: string | null;
  catalog: string | null;
  timeout_s: number | null;
  // The shared saved query this worksheet saves into, if any.
  saved_query_id: string | null;
  // The last run, so a reload restores its results.
  last_query_id: string | null;
  is_open: boolean;
  tab_position: number;
  // Counts content edits (sql, title); sent back as `base_version`.
  version: number;
  created_at: string;
  updated_at: string;
}

// Versioned fields: a stale `base_version` is a 409 rather than an overwrite.
export type WorksheetContent = Partial<Pick<Worksheet, "sql" | "title">>;

// Last-write-wins fields.
export type WorksheetMeta = Partial<
  Pick<
    Worksheet,
    | "agent_id"
    | "catalog"
    | "timeout_s"
    | "saved_query_id"
    | "last_query_id"
    | "is_open"
    | "tab_position"
  >
>;

export interface WorksheetCreate {
  title?: string;
  sql?: string;
  agent_id?: string | null;
  catalog?: string | null;
  timeout_s?: number | null;
  saved_query_id?: string | null;
  is_open?: boolean;
}
