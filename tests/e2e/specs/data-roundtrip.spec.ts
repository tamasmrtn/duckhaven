/** Insert → query → integrity round-trips through the full stack. */
import { expect, test } from "../fixtures/test";

test.beforeEach(async ({ worksheetPage }) => {
  await worksheetPage.goto();
});

test("renders a SELECT result grid (result-fetch path) @smoke", async ({ worksheetPage }) => {
  const rows = await worksheetPage.runAndReadRows("SELECT 42 AS answer, 'hello' AS greeting");
  expect(rows).toEqual([["42", "hello"]]);
});

test("create → insert → read-back preserves edge-case values", async ({ worksheetPage }) => {
  const table = `qa_${Date.now()}`;
  await worksheetPage.runAndReadRows(
    `CREATE TABLE ${table} (id BIGINT, name VARCHAR, bal DECIMAL(12,2))`,
  );
  await worksheetPage.runAndReadRows(
    `INSERT INTO ${table} VALUES (1,'O''Brien',1000.50),(2,'日本語',-50),(3,NULL,0)`,
  );
  const rows = await worksheetPage.runAndReadRows(`SELECT * FROM ${table} ORDER BY id`);
  expect(rows).toEqual([
    ["1", "O'Brien", "1000.5"],
    ["2", "日本語", "-50"],
    ["3", "NULL", "0"],
  ]);
  await worksheetPage.runAndReadRows(`DROP TABLE ${table}`);
});

test("aggregates over a large dataset are correct", async ({ worksheetPage }) => {
  const table = `qa_${Date.now()}`;
  await worksheetPage.runAndReadRows(
    `CREATE TABLE ${table} AS SELECT i AS id FROM generate_series(1,50000) AS s(i)`,
  );
  const rows = await worksheetPage.runAndReadRows(`SELECT COUNT(*), SUM(id) FROM ${table}`);
  expect(rows).toEqual([["50000", "1250025000"]]);
  await worksheetPage.runAndReadRows(`DROP TABLE ${table}`);
});
