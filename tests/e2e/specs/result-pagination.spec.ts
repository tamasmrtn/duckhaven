/** Result-grid pagination: large results load 100 rows/page on scroll. */
import { expect, test } from "../fixtures/test";

test.beforeEach(async ({ worksheetPage }) => {
  await worksheetPage.goto();
});

test("a large result pages 100 rows at a time as you scroll @smoke", async ({
  worksheetPage,
}) => {
  await worksheetPage.run("SELECT n FROM range(250) t(n) ORDER BY n");

  // The first request returns a single 100-row page (the control plane fetches
  // only that window from the agent, never the whole result).
  await expect.poll(() => worksheetPage.rowCount()).toBe(100);

  // Scrolling to the bottom pulls the remaining pages (100 + 50) on demand
  // until all 250 rows are loaded.
  const total = await worksheetPage.loadRowsUntil(250);
  expect(total).toBe(250);
});
