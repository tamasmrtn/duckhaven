export interface RecentlyViewedEntry {
  type: "table" | "schema" | "saved_query";
  catalog: string;
  schema?: string;
  name: string;
  viewedAt: number;
}

const MAX_ENTRIES = 8;

function storageKey(ws: string) {
  return `dh-recently-viewed-${ws}`;
}

function entryKey(
  entry: Pick<RecentlyViewedEntry, "type" | "catalog" | "schema" | "name">,
) {
  return `${entry.type}:${entry.catalog}:${entry.schema ?? ""}:${entry.name}`;
}

/** Record an object as recently viewed, deduping by (type, catalog, schema,
 * name) and pushing it to the front. Caps at MAX_ENTRIES, oldest dropped. */
export function recordRecentlyViewed(
  ws: string,
  entry: Omit<RecentlyViewedEntry, "viewedAt">,
) {
  const existing = getRecentlyViewed(ws);
  const key = entryKey(entry);
  const next = [
    { ...entry, viewedAt: Date.now() },
    ...existing.filter((e) => entryKey(e) !== key),
  ].slice(0, MAX_ENTRIES);
  localStorage.setItem(storageKey(ws), JSON.stringify(next));
}

export function getRecentlyViewed(ws: string): RecentlyViewedEntry[] {
  try {
    const raw = localStorage.getItem(storageKey(ws));
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as RecentlyViewedEntry[]) : [];
  } catch {
    return [];
  }
}
