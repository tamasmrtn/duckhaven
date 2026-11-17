/** Deep linking, browser refresh, and route/state persistence. */
import { expect, test } from "../fixtures/test";
import { ADMIN_STORAGE_STATE, BASE_URL, WS_SLUG } from "../helpers";

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

test.describe("the navigation layout", () => {
  test.afterEach(async ({ page }) => {
    // Every spec signs in as the same admin, so put the layout back.
    await page.request.patch(`${BASE_URL}/api/me`, { data: { sidebar: null } });
  });

  test("collapsing it is saved on the account, not just this browser", async ({
    page,
    browser,
  }) => {
    await page.goto(`${BASE_URL}/${WS_SLUG}/catalog`);
    const nav = page.getByRole("navigation", { name: "Main navigation" });
    await expect(nav).toHaveAttribute("data-mode", "expanded");

    await page.getByRole("button", { name: "Toggle navigation" }).click();
    await expect(nav).toHaveAttribute("data-mode", "collapsed");
    await page.reload();
    await expect(nav).toHaveAttribute("data-mode", "collapsed");

    // Another browser, with none of this one's local storage.
    const other = await browser.newContext({ storageState: ADMIN_STORAGE_STATE });
    const otherPage = await other.newPage();
    await otherPage.goto(`${BASE_URL}/${WS_SLUG}/catalog`);
    await expect(
      otherPage.getByRole("navigation", { name: "Main navigation" }),
    ).toHaveAttribute("data-mode", "collapsed");
    await other.close();
  });

  test("Cmd or Ctrl+B collapses and expands it", async ({ page }) => {
    await page.goto(`${BASE_URL}/${WS_SLUG}/catalog`);
    const nav = page.getByRole("navigation", { name: "Main navigation" });
    await expect(nav).toHaveAttribute("data-mode", "expanded");

    await page.keyboard.press("ControlOrMeta+b");
    await expect(nav).toHaveAttribute("data-mode", "collapsed");
    await page.keyboard.press("ControlOrMeta+b");
    await expect(nav).toHaveAttribute("data-mode", "expanded");
  });

  test("expand on hover opens over the page without moving it", async ({ page }) => {
    await page.request.patch(`${BASE_URL}/api/me`, { data: { sidebar: "hover" } });
    await page.goto(`${BASE_URL}/${WS_SLUG}/catalog`);
    const nav = page.getByRole("navigation", { name: "Main navigation" });
    await expect(nav).toHaveAttribute("data-mode", "hover");
    const before = await page.locator("main").boundingBox();

    await nav.hover();
    await expect(nav).toHaveAttribute("data-open", "true");
    const after = await page.locator("main").boundingBox();
    expect(after?.x).toBe(before?.x);

    await page.keyboard.press("Escape");
    await expect(nav).not.toHaveAttribute("data-open");
  });
});

test("the old Connections address opens History's Connections view", async ({ page }) => {
  await page.goto(`${BASE_URL}/${WS_SLUG}/sessions`);

  await expect(page).toHaveURL(new RegExp(`/${WS_SLUG}/history\\?view=connections$`));
  await expect(page.getByRole("group", { name: "History view" })).toBeVisible();
});

test.describe("the theme", () => {
  test.afterEach(async ({ page }) => {
    // Every spec signs in as the same admin, so put the theme back.
    await page.request.patch(`${BASE_URL}/api/me`, { data: { theme: null } });
  });

  test("a theme chosen in Settings follows the account to another browser", async ({
    page,
    browser,
  }) => {
    await page.goto(`${BASE_URL}/${WS_SLUG}/settings`);
    await page.getByRole("tab", { name: "Account" }).click();
    await page.getByRole("radiogroup", { name: "Theme" }).getByRole("radio", { name: "Dark" }).click();
    await expect(page.locator("html")).toHaveClass(/\bdark\b/);

    // Another browser, with none of this one's local storage.
    const other = await browser.newContext({
      storageState: ADMIN_STORAGE_STATE,
      colorScheme: "light",
    });
    const otherPage = await other.newPage();
    await otherPage.goto(`${BASE_URL}/${WS_SLUG}/catalog`);
    await expect(otherPage.locator("html")).toHaveClass(/\bdark\b/);
    await other.close();
  });
});
