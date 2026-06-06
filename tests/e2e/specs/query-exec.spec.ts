/** Query execution edges: success, invalid SQL, guard rejection, cancellation. */
import { expect, test } from "@playwright/test";

import { BASE_URL, WS_SLUG, login, runQuery, setMonacoValue } from "../helpers";

test.beforeEach(async ({ page }) => {
  await login(page);
  await page.goto(`${BASE_URL}/${WS_SLUG}/worksheets`);
});

test("a successful query reports done", async ({ page }) => {
  await runQuery(page, "SELECT 1 AS ok");
  await expect(page.getByText(/done \d+/).first()).toBeVisible();
});

test("invalid SQL is rejected with a 422", async ({ page }) => {
  const resp = page.waitForResponse(
    (r) => r.url().includes("/queries") && r.request().method() === "POST",
  );
  await setMonacoValue(page, "SELECT * FORM nope");
  await page.locator('[aria-label="Run query (⌘↵)"]').click();
  expect((await resp).status()).toBe(422);
});

test("sandbox-escape statements (SET) are blocked by the guard", async ({ page }) => {
  const resp = page.waitForResponse(
    (r) => r.url().includes("/queries") && r.request().method() === "POST",
  );
  await setMonacoValue(page, "SET memory_limit='1GB'");
  await page.locator('[aria-label="Run query (⌘↵)"]').click();
  expect((await resp).status()).toBe(422);
  const body = await (await resp).json();
  expect(JSON.stringify(body)).toContain("Disallowed statement");
  // The error renders as readable text, never "[object Object]".
  await expect(page.locator("body")).not.toContainText("[object Object]");
});

test("a running query can be cancelled", async ({ page }) => {
  await setMonacoValue(page, "SELECT count(*) FROM range(500000000) t(i)");
  await page.locator('[aria-label="Run query (⌘↵)"]').click();
  const cancel = page.locator("button", { hasText: "Cancel" });
  await cancel.click();
  await expect(page.getByRole("status").first()).toHaveText(/cancel/i, { timeout: 15_000 });
});
