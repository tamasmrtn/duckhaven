/** Insert → query → integrity round-trips through the full stack. */
import { expect, test } from "@playwright/test";

import { BASE_URL, WS_SLUG, login, runQueryAndReadRows } from "../helpers";

test.beforeEach(async ({ page }) => {
  await login(page);
  await page.goto(`${BASE_URL}/${WS_SLUG}/worksheets`);
});

test("renders a SELECT result grid (result-fetch path)", async ({ page }) => {
  const rows = await runQueryAndReadRows(page, "SELECT 42 AS answer, 'hello' AS greeting");
  expect(rows).toEqual([["42", "hello"]]);
});

test("create → insert → read-back preserves edge-case values", async ({ page }) => {
  const table = `qa_${Date.now()}`;
  await runQueryAndReadRows(
    page,
    `CREATE TABLE ${table} (id BIGINT, name VARCHAR, bal DECIMAL(12,2))`,
  );
  await runQueryAndReadRows(
    page,
    `INSERT INTO ${table} VALUES (1,'O''Brien',1000.50),(2,'日本語',-50),(3,NULL,0)`,
  );
  const rows = await runQueryAndReadRows(page, `SELECT * FROM ${table} ORDER BY id`);
  expect(rows).toEqual([
    ["1", "O'Brien", "1000.5"],
    ["2", "日本語", "-50"],
    ["3", "NULL", "0"],
  ]);
  await runQueryAndReadRows(page, `DROP TABLE ${table}`);
});

test("aggregates over a large dataset are correct", async ({ page }) => {
  const table = `qa_${Date.now()}`;
  await runQueryAndReadRows(
    page,
    `CREATE TABLE ${table} AS SELECT i AS id FROM generate_series(1,50000) AS s(i)`,
  );
  const rows = await runQueryAndReadRows(page, `SELECT COUNT(*), SUM(id) FROM ${table}`);
  expect(rows).toEqual([["50000", "1250025000"]]);
  await runQueryAndReadRows(page, `DROP TABLE ${table}`);
});
