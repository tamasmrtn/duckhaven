/** Catalog browsing: a table created via DDL appears, shows schema, previews. */
import { expect, test } from "../fixtures/test";
import { DEFAULT_CATALOG, DEFAULT_SCHEMA } from "../helpers";

test("a table created via DDL appears, previews data, and can be dropped", async ({
  page,
  worksheetPage,
  catalogPage,
}) => {
  const table = `qa_cat_${Date.now()}`;

  await worksheetPage.goto();
  await worksheetPage.runAndReadRows(`CREATE TABLE ${table} (id BIGINT, label VARCHAR)`);
  await worksheetPage.runAndReadRows(`INSERT INTO ${table} VALUES (7, 'seven')`);

  // The table is listed under the default catalog's namespace.
  await catalogPage.goto();
  await catalogPage.expandCatalog();
  await expect(catalogPage.tableLink(table)).toBeVisible();

  // The detail view previews rows through the agent result server (proxy_rows)
  // and shows the column schema.
  await catalogPage.gotoTable(DEFAULT_CATALOG, DEFAULT_SCHEMA, table);
  await expect(page.getByText("7")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator("body")).toContainText("label");

  // Clean up.
  await worksheetPage.goto();
  await worksheetPage.runAndReadRows(`DROP TABLE ${table}`);
});
