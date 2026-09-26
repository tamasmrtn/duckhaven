/** Server worksheets: autosave, per-tab results, and saved-query round trips. */
import { expect, test } from "../fixtures/test";
import { setMonacoValue } from "../helpers";

test("a worksheet's SQL survives a reload @smoke", async ({ page, worksheetPage }) => {
  await worksheetPage.goto();
  await worksheetPage.newTabButton.click();
  const marker = `SELECT ${Date.now()} AS autosaved`;
  await setMonacoValue(page, marker);
  // Autosave is debounced; wait for it to settle before reloading.
  await expect(page.getByText("Saved", { exact: true })).toBeVisible({ timeout: 10_000 });
  await page.waitForTimeout(1500);

  await page.reload();

  await expect
    .poll(() =>
      page.evaluate(() => (window as any).monaco?.editor?.getEditors?.()[0]?.getValue()),
    )
    .toBe(marker);
});

test("each tab keeps its own results", async ({ page, worksheetPage }) => {
  await worksheetPage.goto();
  await worksheetPage.newTabButton.click();
  const rows = await worksheetPage.runAndReadRows("SELECT 41 + 1 AS answer");
  expect(rows).toEqual([["42"]]);

  await worksheetPage.newTabButton.click();
  await expect(page.getByText("No results yet.")).toBeVisible();

  await worksheetPage.worksheetTabs.nth(-2).click();
  await expect(page.locator("table tbody tr")).toHaveCount(1);
});

test("a saved query reopens linked and saves in place", async ({ page, worksheetPage }) => {
  const name = `qa saved ${Date.now()}`;
  await worksheetPage.goto();
  await worksheetPage.newTabButton.click();
  await setMonacoValue(page, "SELECT 1 AS one");

  await page.getByRole("button", { name: "Save…" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Name").fill(name);
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(dialog).toBeHidden();
  const tab = page.getByRole("tab", { name: new RegExp(name) });
  await expect(tab).toBeVisible();

  // Editing marks it unsaved against the shared query; Save updates it in place.
  await setMonacoValue(page, "SELECT 2 AS two");
  await expect(tab.getByLabel("unsaved changes to saved query")).toBeVisible();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(tab.getByLabel("unsaved changes to saved query")).toBeHidden();

  // Closing it keeps it reachable from the rail.
  await tab.getByRole("button", { name: `Close ${name}` }).click();
  await page.getByRole("group", { name: "Sidebar" }).getByRole("button", { name: "Worksheets" }).click();
  await page.getByRole("list", { name: "My worksheets" }).getByRole("button", { name: new RegExp(`^${name}`) }).click();
  await expect(page.getByRole("tab", { name: new RegExp(name) })).toBeVisible();
});
