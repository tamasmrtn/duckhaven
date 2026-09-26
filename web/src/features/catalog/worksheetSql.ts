// SQL templates for the catalog's "open in worksheet" actions. Opening them is
// `useOpenInWorksheet` (features/worksheet/openInWorksheet.ts).

const quote = (ident: string) => `"${ident.replace(/"/g, '""')}"`;

// Fully-qualify a table reference. With a catalog it emits
// `"catalog"."schema"."table"` so the SQL resolves regardless of the
// worksheet's active catalog (cross-catalog safe); without one it stays
// `"schema"."table"` and binds against the active catalog.
function ref(schema: string, table: string, catalog?: string): string {
  const tail = `${quote(schema)}.${quote(table)}`;
  return catalog ? `${quote(catalog)}.${tail}` : tail;
}

export function selectTemplate(
  schema: string,
  table: string,
  catalog?: string,
): string {
  return `SELECT * FROM ${ref(schema, table, catalog)} LIMIT 100;`;
}

export function alterTemplate(
  schema: string,
  table: string,
  catalog?: string,
): string {
  return `ALTER TABLE ${ref(schema, table, catalog)} ADD COLUMN new_column VARCHAR;`;
}

// Iceberg time-travel ("query at this snapshot"). DuckDB's `AT (...)` clause
// reads the table *as of* the given point — snapshot id is exact; timestamp
// resolves to the snapshot in effect at that instant. There is no BEFORE in
// DuckDB, so the UI is labelled "as of", never "before".
export function snapshotByVersionTemplate(
  schema: string,
  table: string,
  snapshotId: string,
  catalog?: string,
): string {
  return `SELECT * FROM ${ref(schema, table, catalog)} AT (VERSION => ${snapshotId}) LIMIT 100;`;
}

export function snapshotByTimestampTemplate(
  schema: string,
  table: string,
  isoTimestamp: string,
  catalog?: string,
): string {
  const ts = isoTimestamp.replace(/'/g, "''");
  return `SELECT * FROM ${ref(schema, table, catalog)} AT (TIMESTAMP => '${ts}') LIMIT 100;`;
}
