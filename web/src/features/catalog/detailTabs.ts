// The catalog detail panes' tab bar, styled like the worksheet's
// Results | Profile bar: the two pages share the catalog tree, so their panes
// share one tab style and size too, as Databricks does for its editor output
// and Catalog Explorer tabs.
export const detailTabsListClass =
  "h-auto w-full shrink-0 justify-start gap-0.5 rounded-none bg-[var(--bg-surface)] px-3 py-1.5";

export const detailTabClass =
  "rounded px-2 py-0.5 text-xs font-medium text-text-secondary shadow-none hover:text-text-primary data-[state=active]:bg-[var(--bg-elevated)] data-[state=active]:text-text-primary data-[state=active]:shadow-none";
