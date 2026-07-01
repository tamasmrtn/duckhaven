/**
 * Auth setup project: authenticate once and persist the admin session so the
 * `authenticated` project's specs reuse it via storageState (no per-test login).
 * Runs after global-setup.ts has ensured the admin + workspace exist.
 */
import { expect, test as setup } from "@playwright/test";

import { ADMIN_EMAIL, ADMIN_PASSWORD, ADMIN_STORAGE_STATE, BASE_URL, WS_SLUG } from "../helpers";

setup("authenticate as admin", async ({ page }) => {
  await page.goto(`${BASE_URL}/login`);
  await page.getByRole("textbox", { name: "Email" }).fill(ADMIN_EMAIL);
  await page.getByRole("textbox", { name: "Password" }).fill(ADMIN_PASSWORD);
  // Exact match so a configured OIDC provider's "Sign in with <IdP>" button
  // does not also match the local sign-in button.
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/${WS_SLUG}/`));
  await page.context().storageState({ path: ADMIN_STORAGE_STATE });
});
