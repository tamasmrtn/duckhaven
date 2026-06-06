/** Abuse cases: double submit, refresh during execution, concurrent tabs. */
import { expect, test } from "@playwright/test";

import { BASE_URL, WS_SLUG, login, runQueryAndReadRows, setMonacoValue } from "../helpers";

test.beforeEach(async ({ page }) => {
  await login(page);
  await page.goto(`${BASE_URL}/${WS_SLUG}/worksheets`);
});

test("double-clicking Run does not corrupt the result", async ({ page }) => {
  await setMonacoValue(page, "SELECT 1 AS one");
  await page.locator('[aria-label="Run query (⌘↵)"]').dblclick();
  await page.getByText(/done \d+/).first().waitFor({ timeout: 15_000 });
  // A single coherent result renders despite the double submit.
  const rows = await page.evaluate(() =>
    [...document.querySelectorAll("table tbody tr")].length,
  );
  expect(rows).toBe(1);
});

test("refreshing during execution recovers to a usable worksheet", async ({ page }) => {
  await setMonacoValue(page, "SELECT count(*) FROM range(200000000) t(i)");
  await page.locator('[aria-label="Run query (⌘↵)"]').click();
  await page.reload();
  // After refresh the editor is usable again and a fresh query succeeds.
  const rows = await runQueryAndReadRows(page, "SELECT 'after-refresh' AS marker");
  expect(rows).toEqual([["after-refresh"]]);
});

test("a second tab opens an independent worksheet", async ({ page }) => {
  await page.locator('[aria-label="New worksheet"]').click();
  await expect(page.locator('[role="tablist"] [role="tab"]')).toHaveCount(2);
  // Each tab runs independently.
  const rows = await runQueryAndReadRows(page, "SELECT 2 AS two");
  expect(rows).toEqual([["2"]]);
});
