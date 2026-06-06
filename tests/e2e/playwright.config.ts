import { defineConfig } from "@playwright/test";

import { BASE_URL } from "./helpers";

/**
 * End-to-end suite for the full DuckHaven compose stack (web + API + agent +
 * Postgres + Polaris + MinIO), served same-origin at BASE_URL. Bring the stack
 * up first (`make compose-up`) and provide the first-boot setup token via
 * DH_SETUP_TOKEN; `global-setup.ts` then guarantees an admin + the analytics
 * workspace exist before any spec runs.
 *
 * On failure CI keeps a trace, screenshot, and video for triage.
 */
export default defineConfig({
  testDir: "./specs",
  globalSetup: "./global-setup.ts",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  outputDir: "test-results",
  use: {
    baseURL: BASE_URL,
    headless: true,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
});
