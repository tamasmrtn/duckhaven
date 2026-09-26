import type { Worksheet, WorksheetCreate } from "@/types/worksheet";

// Worksheet tabs used to live only in this browser. These are the keys they
// were kept under; the first load after the move to server worksheets uploads
// them and then removes the keys.
const tabsKey = (ws: string) => `dh-worksheets-${ws}`;
const activeTabKey = (ws: string) => `dh-active-tab-${ws}`;
const activeQueryKey = (ws: string) => `dh-active-query-${ws}`;
const pendingKey = (ws: string) => `dh-pending-sql-${ws}`;

interface LegacyTab {
  id: string;
  title: string;
  sql: string;
  savedQueryId?: string;
}

function readTabs(ws: string): LegacyTab[] {
  try {
    const raw = localStorage.getItem(tabsKey(ws));
    const parsed = raw ? (JSON.parse(raw) as unknown) : null;
    return Array.isArray(parsed)
      ? parsed.filter(
          (t): t is LegacyTab =>
            !!t && typeof t.id === "string" && typeof t.sql === "string",
        )
      : [];
  } catch {
    return [];
  }
}

function writeTabs(ws: string, tabs: LegacyTab[]): void {
  try {
    if (tabs.length) localStorage.setItem(tabsKey(ws), JSON.stringify(tabs));
    else localStorage.removeItem(tabsKey(ws));
  } catch {
    // Unavailable storage: the tabs are uploaded; only a resume is lost.
  }
}

// The placeholder every workspace used to start with: nothing worth keeping.
const isBlank = (t: LegacyTab) =>
  !t.sql.trim() &&
  !t.savedQueryId &&
  ["untitled", "from catalog", "saved query"].includes(t.title);

/**
 * Upload this browser's legacy tabs for `ws` as worksheets, in order.
 *
 * Each tab is removed from storage as soon as it is created, so a failure
 * part-way resumes where it stopped rather than duplicating what already went
 * up. Returns the created worksheets and the one that replaces the tab that
 * was active, if any.
 */
export async function migrateLegacyTabs(
  ws: string,
  opts: {
    create: (body: WorksheetCreate) => Promise<Worksheet>;
    // A link to a saved query that no longer exists is dropped, not sent.
    savedQueryIds: Set<string>;
  },
): Promise<{ created: Worksheet[]; activeId: string | null }> {
  let remaining = readTabs(ws);
  let activeLegacy: string | null = null;
  try {
    activeLegacy = sessionStorage.getItem(activeTabKey(ws));
  } catch {
    // ignore
  }
  const created: Worksheet[] = [];
  let activeId: string | null = null;

  while (remaining.length) {
    const [tab, ...rest] = remaining;
    if (!isBlank(tab)) {
      const linked =
        tab.savedQueryId && opts.savedQueryIds.has(tab.savedQueryId)
          ? tab.savedQueryId
          : null;
      const sheet = await opts.create({
        title: tab.title?.trim() || "Untitled",
        sql: tab.sql,
        saved_query_id: linked,
      });
      created.push(sheet);
      if (tab.id === activeLegacy) activeId = sheet.id;
    }
    remaining = rest;
    writeTabs(ws, remaining);
  }

  try {
    localStorage.removeItem(pendingKey(ws));
    sessionStorage.removeItem(activeQueryKey(ws));
    if (activeLegacy && !activeLegacy.startsWith("wk")) {
      sessionStorage.removeItem(activeTabKey(ws));
    }
  } catch {
    // ignore
  }
  return { created, activeId };
}

export function hasLegacyTabs(ws: string): boolean {
  return readTabs(ws).length > 0;
}
