/** Abuse cases: double submit, refresh during execution, concurrent tabs. */
import { expect, test } from "../fixtures/test";

test.beforeEach(async ({ worksheetPage }) => {
  await worksheetPage.goto();
});

test("double-clicking Run does not corrupt the result", async ({ page, worksheetPage }) => {
  await worksheetPage.setSql("SELECT 1 AS one");
  await worksheetPage.runButton.dblclick();
  await page.getByText(/done \d+/).first().waitFor({ timeout: 15_000 });
  // A single coherent result renders despite the double submit.
  expect(await worksheetPage.rowCount()).toBe(1);
});

test("refreshing during execution recovers to a usable worksheet", async ({
  page,
  worksheetPage,
}) => {
  await worksheetPage.setSql("SELECT count(*) FROM range(200000000) t(i)");
  await worksheetPage.runButton.click();
  await page.reload();
  // After refresh the editor is usable again and a fresh query succeeds.
  const rows = await worksheetPage.runAndReadRows("SELECT 'after-refresh' AS marker");
  expect(rows).toEqual([["after-refresh"]]);
});

test("a second tab opens an independent worksheet", async ({ page, worksheetPage }) => {
  await worksheetPage.newTabButton.click();
  await expect(page.locator('[role="tablist"] [role="tab"]')).toHaveCount(2);
  // Each tab runs independently.
  const rows = await worksheetPage.runAndReadRows("SELECT 2 AS two");
  expect(rows).toEqual([["2"]]);
});
