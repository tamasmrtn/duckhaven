/** Login, logout, session persistence, and credential validation. */
import { expect, test } from "../fixtures/test";
import { ADMIN_EMAIL, BASE_URL, WS_SLUG } from "../helpers";

test("valid credentials log in and route to the workspace @smoke", async ({ page, loginPage }) => {
  await loginPage.loginAsAdmin();
  await expect(page).toHaveURL(new RegExp(`/${WS_SLUG}/`));
});

test("invalid credentials are rejected with an error", async ({ page, loginPage }) => {
  await loginPage.login(ADMIN_EMAIL, "WrongPassword");
  await expect(page).toHaveURL(/\/login/);
  await expect(loginPage.error).toBeVisible();
  await expect(loginPage.error).not.toBeEmpty();
});

test("session persists across a reload", async ({ page, loginPage }) => {
  await loginPage.loginAsAdmin();
  await page.reload();
  await expect(page).not.toHaveURL(/\/login/);
  await expect(page).toHaveURL(new RegExp(`/${WS_SLUG}/`));
});

test("logging out returns to the login screen and protects routes", async ({ page, loginPage }) => {
  await loginPage.loginAsAdmin();
  // The user menu (trigger labelled with the admin's name) holds "Sign out".
  // Scope to the banner so the regex can't also match the nav "Admin" button.
  await page.getByRole("banner").getByRole("button", { name: /Account|Admin/i }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);

  // A protected deep link now bounces back to login.
  await page.goto(`${BASE_URL}/${WS_SLUG}/worksheets`);
  await expect(page).toHaveURL(/\/login/);
});
