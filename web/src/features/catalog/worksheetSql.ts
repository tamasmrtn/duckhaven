// Helpers for routing a catalog action into a SQL worksheet (Snowsight-style):
// the catalog UI builds templated SQL, stashes it, and navigates to the
// worksheet, which seeds a new tab from the stash on mount.

const pendingKey = (ws: string) => `dh-pending-sql-${ws}`;

// A worksheet hand-off carries the SQL plus, when opened from a saved query, the
// saved default agent and the saved query id (so a run can stamp last_run_at).
export interface PendingQuery {
  sql: string;
  agentId?: string;
  savedQueryId?: string;
}

// Catalog actions stash plain SQL; saved queries use stashWorksheetQuery below.
export function stashWorksheetSql(ws: string, sql: string): void {
  stashWorksheetQuery(ws, { sql });
}

export function stashWorksheetQuery(ws: string, payload: PendingQuery): void {
  try {
    localStorage.setItem(pendingKey(ws), JSON.stringify(payload));
  } catch {
    // ignore unavailable storage
  }
}

export function takePendingQuery(ws: string): PendingQuery | null {
  try {
    const raw = localStorage.getItem(pendingKey(ws));
    if (!raw) return null;
    localStorage.removeItem(pendingKey(ws));
    try {
      const parsed = JSON.parse(raw) as PendingQuery;
      if (parsed && typeof parsed.sql === "string") return parsed;
    } catch {
      // tolerate a legacy plain-string value
    }
    return { sql: raw };
  } catch {
    return null;
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
