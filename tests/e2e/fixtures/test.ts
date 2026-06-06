/**
 * The project's `test` object, extended with Page Object Model fixtures.
 *
 * Specs import `{ test, expect }` from here and receive ready-built POMs
 * (`loginPage`, `worksheetPage`, …). Authentication is handled by the `setup`
 * project + stored `storageState` (see playwright.config.ts), so authenticated
 * specs no longer log in per-test.
 */
import { test as base, expect } from "@playwright/test";

import { AdminAgentsPage } from "../pages/AdminAgentsPage";
import { CatalogPage } from "../pages/CatalogPage";
import { LoginPage } from "../pages/LoginPage";
import { WorksheetPage } from "../pages/WorksheetPage";

type Pages = {
  loginPage: LoginPage;
  worksheetPage: WorksheetPage;
  catalogPage: CatalogPage;
  adminAgentsPage: AdminAgentsPage;
};

export const test = base.extend<Pages>({
  loginPage: async ({ page }, use) => {
    await use(new LoginPage(page));
  },
  worksheetPage: async ({ page }, use) => {
    await use(new WorksheetPage(page));
  },
  catalogPage: async ({ page }, use) => {
    await use(new CatalogPage(page));
  },
  adminAgentsPage: async ({ page }, use) => {
    await use(new AdminAgentsPage(page));
  },
});

export { expect };
