/** Login, logout, session persistence, and credential validation. */
import { expect, test } from "@playwright/test";

import { ADMIN_EMAIL, ADMIN_PASSWORD, BASE_URL, WS_SLUG, login } from "../helpers";

test("valid credentials log in and route to the workspace", async ({ page }) => {
  await login(page);
  await expect(page).toHaveURL(new RegExp(`/${WS_SLUG}/`));
});

test("invalid credentials are rejected with an error", async ({ page }) => {
  await page.goto(`${BASE_URL}/login`);
  await page.getByRole("textbox", { name: "Email" }).fill(ADMIN_EMAIL);
  await page.getByRole("textbox", { name: "Password" }).fill("WrongPassword");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/login/);
  await expect(page.locator('[role="alert"]').first()).toBeVisible();
  await expect(page.locator('[role="alert"]').first()).not.toBeEmpty();
});

test("session persists across a reload", async ({ page }) => {
  await login(page);
  await page.reload();
  await expect(page).not.toHaveURL(/\/login/);
  await expect(page).toHaveURL(new RegExp(`/${WS_SLUG}/`));
});

test("logging out returns to the login screen and protects routes", async ({ page }) => {
  await login(page);
  // The user menu (trigger labelled with the admin's name) holds "Sign out".
  await page.getByRole("button", { name: /Account|Admin/i }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);

  // A protected deep link now bounces back to login.
  await page.goto(`${BASE_URL}/${WS_SLUG}/worksheets`);
  await expect(page).toHaveURL(/\/login/);
});
