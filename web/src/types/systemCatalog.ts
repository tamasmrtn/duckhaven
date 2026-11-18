/** The `duckhaven` system catalog's tables, as `GET /workspaces/{ws}/system-catalog` lists them. */
export interface SystemColumn {
  name: string;
  type: string;
}

export interface SystemTable {
  schema_name: string;
  name: string;
  description: string;
  columns: SystemColumn[];
}

export interface SystemCatalog {
  catalog: string;
  /** False while its database cannot be reached: a query on it fails. */
  available: boolean;
  tables: SystemTable[];
}
