/**
 * Snapshot history + "query at this snapshot": the History tab reads the live
 * Iceberg snapshot log from Polaris, and a time-travel query runs on the agent
 * against the attached REST catalog (validates the `AT (VERSION => …)` clause
 * end-to-end — the integration the unit/component layers can only mock).
 */
import { expect, test } from "../fixtures/test";
import { DEFAULT_CATALOG, DEFAULT_SCHEMA } from "../helpers";

test("History tab lists commits and time-travels to a past snapshot", async ({
  page,
  worksheetPage,
  catalogPage,
}) => {
  const table = `qa_hist_${Date.now()}`;

  // Two writes ⇒ two Iceberg snapshots: A (one row) then B (two rows).
  await worksheetPage.goto();
  await worksheetPage.runAndReadRows(`CREATE TABLE ${table} (id BIGINT)`);
  await worksheetPage.runAndReadRows(`INSERT INTO ${table} VALUES (1)`);
  await worksheetPage.runAndReadRows(`INSERT INTO ${table} VALUES (2)`);

  // History tab reads the snapshot log live from Polaris.
  await catalogPage.gotoTable(DEFAULT_CATALOG, DEFAULT_SCHEMA, table);
  await page.getByRole("tab", { name: /history/i }).click();

  const panel = page.getByRole("tabpanel");
  await expect(panel.getByText("current")).toBeVisible({ timeout: 15_000 });
  const queryButtons = panel.getByRole("button", {
    name: /query at this snapshot/i,
  });
  // At least the two commits we made are listed.
  await expect(async () => {
    expect(await queryButtons.count()).toBeGreaterThanOrEqual(2);
  }).toPass({ timeout: 15_000 });

  // The oldest snapshot is the last row; its first cell is the snapshot id
  // (no "current" badge). Time-travel to it must see the pre-second-insert
  // state: exactly one row.
  const oldSnapshotId = (
    await panel.locator("table tbody tr td:first-child").last().innerText()
  ).trim();
  expect(oldSnapshotId).toMatch(/^\d+$/);

  // The row button itself routes into a worksheet (affordance wiring).
  await queryButtons.last().click();
  await expect(page).toHaveURL(/\/worksheets/);

  // Run the time-travel query deterministically against the agent.
  const rows = await worksheetPage.runAndReadRows(
    `SELECT id FROM ${table} AT (VERSION => ${oldSnapshotId}) ORDER BY id`,
  );
  expect(rows).toEqual([["1"]]);

  await worksheetPage.runAndReadRows(`DROP TABLE ${table}`);
});
