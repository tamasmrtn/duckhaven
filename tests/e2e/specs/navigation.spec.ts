/** Deep linking, browser refresh, and route/state persistence. */
import { expect, test } from "../fixtures/test";
import { BASE_URL, WS_SLUG } from "../helpers";

test("deep-link refresh keeps the session and route", async ({ page }) => {
  const url = `${BASE_URL}/${WS_SLUG}/catalog`;
  await page.goto(url);
  await page.reload();
  await expect(page).toHaveURL(url);
  await expect(page.getByRole("heading", { name: "Catalog" })).toBeVisible();
});

test("an unknown workspace route shows a not-found state", async ({ page }) => {
  await page.goto(`${BASE_URL}/${WS_SLUG}-missing/catalog`);
  await expect(page.locator("body")).toContainText("Page not found");
});

test("the main routes are reachable and do not bounce to login", async ({ page }) => {
  for (const path of ["worksheets", "catalog", "saved-queries", "history", "compute"]) {
    await page.goto(`${BASE_URL}/${WS_SLUG}/${path}`);
    await expect(page).not.toHaveURL(/\/login/);
    await expect(page.locator("body")).not.toContainText("Page not found");
  }
});
