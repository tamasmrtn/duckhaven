/** Admin → Agents: visibility, health, and advertised capabilities. */
import { expect, test } from "@playwright/test";

import { BASE_URL, WS_SLUG, login } from "../helpers";

test.beforeEach(async ({ page }) => {
  await login(page);
});

test("the bundled agent is listed as healthy with storage extensions", async ({ page }) => {
  await page.goto(`${BASE_URL}/${WS_SLUG}/admin/agents`);

  const row = page.locator("table tbody tr").first();
  await expect(row).toBeVisible();

  // Health is shown as a status dot with an accessible label.
  const dot = page.locator('table tbody td [role="img"]').first();
  await expect(dot).toHaveAttribute("aria-label", "healthy");

  // Storage extensions must be advertised, else every dispatch is rejected.
  await expect(row).toContainText("httpfs");
  await expect(row).toContainText("iceberg");
});
