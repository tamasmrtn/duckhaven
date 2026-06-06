import { defineConfig } from "@playwright/test";

import { ADMIN_STORAGE_STATE, BASE_URL } from "./helpers";

/**
 * End-to-end suite for the full DuckHaven compose stack (web + API + agent +
 * Postgres + Polaris + MinIO), served same-origin at BASE_URL. Bring the stack
 * up first (`make compose-up`) and provide the first-boot setup token via
 * DH_SETUP_TOKEN; `global-setup.ts` then guarantees an admin + the analytics
 * workspace exist before any spec runs.
 *
 * Projects:
 *  - `setup`           — logs in once, saves admin storageState.
 *  - `unauthenticated` — bootstrap + auth specs (must start signed out).
 *  - `authenticated`   — everything else, reusing the stored session.
 *
 * Tag a critical subset `@smoke`; run the fast lane with `--grep @smoke`.
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
  projects: [
    { name: "setup", testMatch: /auth\.setup\.ts/ },
    {
      name: "unauthenticated",
      testMatch: /(bootstrap|auth)\.spec\.ts/,
    },
    {
      name: "authenticated",
      testIgnore: /(auth\.setup\.ts|(bootstrap|auth)\.spec\.ts)/,
      dependencies: ["setup"],
      use: { storageState: ADMIN_STORAGE_STATE },
    },
  ],
});
