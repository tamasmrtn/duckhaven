/**
 * Post-execution query profile UI: after a query finishes, the Profile tab
 * renders a query-level summary + an interactive operator tree, flags
 * inefficiencies, and is reachable from the History view. Exercises the full
 * stack: agent JSON profiling → QUERY_DONE → persisted profile → REST → UI.
 */
import { expect, test } from "../fixtures/test";

// A scan-heavy aggregation: reads many rows, returns few groups (scan blow-up),
// over a blocking GROUP BY + ORDER BY (real operator tree + timings).
const HEAVY_SQL =
  "SELECT g, count(*) c FROM (SELECT i % 50 g FROM range(400000) t(i)) GROUP BY g ORDER BY c DESC";

test("the Profile tab shows the summary and operator tree @smoke", async ({
  page,
  worksheetPage,
}) => {
  await worksheetPage.goto();
  await worksheetPage.run(HEAVY_SQL);

  await worksheetPage.openProfile();

  // Query-level summary strip.
  await expect(page.getByText("Latency", { exact: true })).toBeVisible();
  await expect(page.getByText("Peak memory", { exact: true })).toBeVisible();

  // Interactive operator tree with a blocking GROUP BY node.
  await expect(page.getByText(/GROUP_BY/).first()).toBeVisible();

  // Collapsing the root hides descendants; expanding restores them. Match the
  // exact "Collapse" label so this targets the profile tree's root, not the
  // catalog sidebar's "Collapse catalog"/"Collapse schema" chevrons.
  const groupBy = page.getByText(/GROUP_BY/).first();
  await page.getByRole("button", { name: "Collapse", exact: true }).first().click();
  await expect(groupBy).toBeHidden();
});

test("a scan-heavy aggregation flags a scan blow-up", async ({ page, worksheetPage }) => {
  await worksheetPage.goto();
  await worksheetPage.run(HEAVY_SQL);
  await worksheetPage.openProfile();
  // 400k rows scanned for 50 returned groups => scan blow-up badge.
  await expect(page.getByText(/Scan blow-up/i).first()).toBeVisible();
});

test("DDL shows the no-profile state", async ({ page, worksheetPage }) => {
  const table = `prof_ddl_${Date.now()}`;
  await worksheetPage.goto();
  await worksheetPage.run(`CREATE TABLE ${table} (id BIGINT)`);
  await worksheetPage.openProfile();
  await expect(page.getByText(/No profile for this query/i)).toBeVisible();
  await worksheetPage.run(`DROP TABLE ${table}`);
});

test("clicking a history row opens the dedicated query-profile page", async ({
  page,
  worksheetPage,
}) => {
  // Seed a query so history has a profiled row to open.
  await worksheetPage.goto();
  await worksheetPage.run(HEAVY_SQL);

  // Navigate to History and click the most recent row.
  await page.getByRole("button", { name: "History" }).click();
  await expect(page).toHaveURL(/\/history/);
  await page.locator("table tbody tr").first().click();

  // A dedicated /$ws/queries/$id page with the operator graph + stats + panels.
  await expect(page).toHaveURL(/\/queries\//);
  await expect(page.getByText("Query profile")).toBeVisible();
  await expect(page.getByText("Latency", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /GROUP_BY/ }).first()).toBeVisible();
  await expect(page.getByText("Most expensive operators")).toBeVisible();
});

test("the worksheet Profile tab links to the full profile page", async ({
  page,
  worksheetPage,
}) => {
  await worksheetPage.goto();
  await worksheetPage.run(HEAVY_SQL);
  await worksheetPage.openProfile();

  await page.getByRole("link", { name: /Open full profile/i }).click();
  await expect(page).toHaveURL(/\/queries\//);
  await expect(page.getByText("Query profile")).toBeVisible();
});
