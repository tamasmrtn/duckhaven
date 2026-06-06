/**
 * Shared constants + low-level bridges for the DuckHaven E2E suite.
 *
 * Page interactions live in Page Object Models under `pages/`; this module only
 * holds env-driven constants and the Monaco editor bridge (the editor surface is
 * not a standard textarea, so it is driven through the Monaco API).
 */
import { type Page } from "@playwright/test";

export const BASE_URL = process.env.DH_BASE_URL ?? "http://localhost:8000";
export const ADMIN_EMAIL = process.env.DH_ADMIN_EMAIL ?? "admin@admin.com";
export const ADMIN_PASSWORD = process.env.DH_ADMIN_PASSWORD ?? "TestPassword123";
export const WS_SLUG = process.env.DH_WS_SLUG ?? "analytics";

// Where the `setup` project saves the authenticated admin storage state, reused
// by the `authenticated` project. Resolved relative to the Playwright cwd
// (tests/e2e). Gitignored.
export const ADMIN_STORAGE_STATE = ".auth/admin.json";

export async function setMonacoValue(page: Page, sql: string): Promise<void> {
  await page.waitForFunction(() => !!(window as any).monaco?.editor?.getEditors?.().length);
  await page.evaluate((value) => {
    (window as any).monaco.editor.getEditors()[0].setValue(value);
  }, sql);
}
