/**
 * First-admin onboarding + setup-token gating.
 *
 * global-setup.ts already created the admin via the API, so the full UI
 * create-flow only runs on a truly fresh stack (skips otherwise, faithfully to
 * the original QA behaviour). The redirect assertion always runs.
 */
import { expect, test } from "@playwright/test";

import { ADMIN_EMAIL, ADMIN_PASSWORD, BASE_URL } from "../helpers";

const SETUP_TOKEN = process.env.DH_SETUP_TOKEN ?? "";

test("setup route redirects to login once an admin exists", async ({ page }) => {
  await page.goto(`${BASE_URL}/setup`);
  // SetupPage bounces to /login when setup is already complete.
  await expect(page).toHaveURL(/\/login/);
});

test("first-admin setup gates on a valid token, then succeeds", async ({ page }) => {
  await page.goto(BASE_URL);
  if (!page.url().includes("/setup")) test.skip(true, "Admin already created");
  test.skip(!SETUP_TOKEN, "DH_SETUP_TOKEN not provided");

  await page.getByRole("textbox", { name: "Setup token" }).fill("wrong-token");
  await page.getByRole("textbox", { name: "Email" }).fill(ADMIN_EMAIL);
  await page.getByRole("textbox", { name: "Password" }).fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "Create admin" }).click();
  await expect(page.getByText(/Invalid or missing setup token/)).toBeVisible();

  await page.getByRole("textbox", { name: "Setup token" }).fill(SETUP_TOKEN);
  await page.getByRole("button", { name: "Create admin" }).click();
  await expect(page).toHaveURL(/\/welcome/);
});
