export interface TableColumn {
  position: number;
  name: string;
  type: string;
  nullable: boolean;
}

export interface CatalogTable {
  name: string;
  schema_name: string;
  workspace_id: string;
  row_count: number | null;
  size_bytes: number | null;
  format: string;
  catalog_commits: boolean;
  owner: string;
  last_write_at: string | null;
  last_write_by: string | null;
  last_write_agent: string | null;
  columns: TableColumn[];
}

export interface CatalogSchema {
  name: string;
  workspace_id: string;
  tables: CatalogTable[];
}
