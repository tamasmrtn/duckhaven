// Whether a single SQL statement is catalog DDL (CREATE / ALTER / DROP), so a
// successful run can refresh the catalog and autocomplete caches. Mirrors the
// DDL heads the backend SQL guard allows. Pure (no Monaco) so it's unit-testable.
export function isDdl(sql: string): boolean {
  return /^\s*(create|alter|drop)\b/i.test(sql);
}
