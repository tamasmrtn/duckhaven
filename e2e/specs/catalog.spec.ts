/** Catalog browsing: a table created via DDL appears, shows schema, previews. */
import { expect, test } from "@playwright/test";

import { BASE_URL, WS_SLUG, login, runQueryAndReadRows } from "../helpers";

test.beforeEach(async ({ page }) => {
  await login(page);
});

test("a table created via DDL appears, previews data, and can be dropped", async ({ page }) => {
  const table = `qa_cat_${Date.now()}`;

  await page.goto(`${BASE_URL}/${WS_SLUG}/worksheets`);
  await runQueryAndReadRows(page, `CREATE TABLE ${table} (id BIGINT, label VARCHAR)`);
  await runQueryAndReadRows(page, `INSERT INTO ${table} VALUES (7, 'seven')`);

  // The table is listed under the workspace's default namespace.
  await page.goto(`${BASE_URL}/${WS_SLUG}/catalog`);
  await page.getByRole("button", { name: WS_SLUG }).click();
  await expect(page.getByRole("link", { name: table })).toBeVisible();

  // The detail view previews rows through the agent result server (proxy_rows)
  // and shows the column schema.
  await page.goto(`${BASE_URL}/${WS_SLUG}/catalog/${WS_SLUG}/${table}`);
  await expect(page.getByText("7")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator("body")).toContainText("label");

  // Clean up.
  await page.goto(`${BASE_URL}/${WS_SLUG}/worksheets`);
  await runQueryAndReadRows(page, `DROP TABLE ${table}`);
});
