/**
 * Shared Playwright helpers for the DuckHaven end-to-end suite.
 *
 * Ported from the exploratory qa/ research: authentication, the Monaco editor
 * bridge, and the run-query flow are defined once. Authenticated specs call
 * `login()` in a beforeEach so each test runs from a clean browser context.
 */
import { expect, type Page } from "@playwright/test";

export const BASE_URL = process.env.DH_BASE_URL ?? "http://localhost:8000";
export const ADMIN_EMAIL = process.env.DH_ADMIN_EMAIL ?? "admin@admin.com";
export const ADMIN_PASSWORD = process.env.DH_ADMIN_PASSWORD ?? "TestPassword123";
export const WS_SLUG = process.env.DH_WS_SLUG ?? "analytics";

export async function login(page: Page): Promise<void> {
  await page.goto(`${BASE_URL}/login`);
  await page.getByRole("textbox", { name: "Email" }).fill(ADMIN_EMAIL);
  await page.getByRole("textbox", { name: "Password" }).fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(new RegExp(`/${WS_SLUG}/`));
}

/** Drive the Monaco editor via its API; the edit surface is not a textarea. */
export async function setMonacoValue(page: Page, sql: string): Promise<void> {
  await page.waitForFunction(
    () => !!(window as any).monaco?.editor?.getEditors?.().length,
  );
  await page.evaluate((value) => {
    (window as any).monaco.editor.getEditors()[0].setValue(value);
  }, sql);
}

export async function runQuery(page: Page, sql: string): Promise<void> {
  await setMonacoValue(page, sql);
  await page.locator('[aria-label="Run query (⌘↵)"]').click();
  await expect(page.getByText(/done \d+/).first()).toBeVisible({ timeout: 30_000 });
}

/** Run a query and return the rendered result grid as a matrix of cell text. */
export async function runQueryAndReadRows(page: Page, sql: string): Promise<string[][]> {
  await runQuery(page, sql);
  return page.evaluate(() =>
    [...document.querySelectorAll("table tbody tr")].map((tr) =>
      [...tr.querySelectorAll("td")].map((td) =>
        (td as HTMLElement).innerText.replace(/^Copy\s*/, "").trim(),
      ),
    ),
  );
}
