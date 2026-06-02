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

const quote = (ident: string) => `"${ident.replace(/"/g, '""')}"`;

export function selectTemplate(schema: string, table: string): string {
  return `SELECT * FROM ${quote(schema)}.${quote(table)} LIMIT 100;`;
}

export function alterTemplate(schema: string, table: string): string {
  return `ALTER TABLE ${quote(schema)}.${quote(table)} ADD COLUMN new_column VARCHAR;`;
}
