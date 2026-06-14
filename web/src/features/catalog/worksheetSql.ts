// Helpers for routing a catalog action into a SQL worksheet (Snowsight-style):
// the catalog UI builds templated SQL, stashes it, and navigates to the
// worksheet, which seeds a new tab from the stash on mount.

const pendingKey = (ws: string) => `dh-pending-sql-${ws}`;

export function stashWorksheetSql(ws: string, sql: string): void {
  try {
    localStorage.setItem(pendingKey(ws), sql);
  } catch {
    // ignore unavailable storage
  }
}

export function takePendingSql(ws: string): string | null {
  try {
    const sql = localStorage.getItem(pendingKey(ws));
    if (sql) localStorage.removeItem(pendingKey(ws));
    return sql;
  } catch {
    return null;
  }
}

// History → worksheet hand-off: stash the clicked query id (same sessionStorage
// key the worksheet seeds `activeQueryId` from) plus a flag telling it to open
// the Profile tab on mount.
const activeQueryKey = (ws: string) => `dh-active-query-${ws}`;
const openProfileKey = (ws: string) => `dh-open-profile-${ws}`;

export function stashHistoryProfile(ws: string, queryId: string): void {
  try {
    sessionStorage.setItem(activeQueryKey(ws), queryId);
    sessionStorage.setItem(openProfileKey(ws), "1");
  } catch {
    // ignore unavailable storage
  }
}

export function takeOpenProfile(ws: string): boolean {
  try {
    const flag = sessionStorage.getItem(openProfileKey(ws));
    if (flag) sessionStorage.removeItem(openProfileKey(ws));
    return flag === "1";
  } catch {
    return false;
  }
}

const quote = (ident: string) => `"${ident.replace(/"/g, '""')}"`;

export function selectTemplate(schema: string, table: string): string {
  return `SELECT * FROM ${quote(schema)}.${quote(table)} LIMIT 100;`;
}

export function alterTemplate(schema: string, table: string): string {
  return `ALTER TABLE ${quote(schema)}.${quote(table)} ADD COLUMN new_column VARCHAR;`;
}

// Iceberg time-travel ("query at this snapshot"). DuckDB's `AT (...)` clause
// reads the table *as of* the given point — snapshot id is exact; timestamp
// resolves to the snapshot in effect at that instant. There is no BEFORE in
// DuckDB, so the UI is labelled "as of", never "before".
export function snapshotByVersionTemplate(
  schema: string,
  table: string,
  snapshotId: string,
): string {
  return `SELECT * FROM ${quote(schema)}.${quote(table)} AT (VERSION => ${snapshotId}) LIMIT 100;`;
}

export function snapshotByTimestampTemplate(
  schema: string,
  table: string,
  isoTimestamp: string,
): string {
  const ts = isoTimestamp.replace(/'/g, "''");
  return `SELECT * FROM ${quote(schema)}.${quote(table)} AT (TIMESTAMP => '${ts}') LIMIT 100;`;
}
