/** The `duckhaven` system catalog's tables, as `GET /workspaces/{ws}/system-catalog` lists them. */
export interface SystemColumn {
  name: string;
  type: string;
}

export interface SystemTable {
  schema_name: string;
  name: string;
  description: string;
  /** The permission reading it takes; null for a table every member reads, its
   * rows narrowed to what they can see. */
  permission: string | null;
  columns: SystemColumn[];
}

export interface SystemCatalog {
  catalog: string;
  /** False while its database cannot be reached: a query on it fails. */
  available: boolean;
  /** Only the tables the caller may read. */
  tables: SystemTable[];
}
