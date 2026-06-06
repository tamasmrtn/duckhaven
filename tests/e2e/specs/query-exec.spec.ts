/** Query execution edges: success, invalid SQL, guard rejection, cancellation. */
import { expect, test } from "../fixtures/test";

test.beforeEach(async ({ worksheetPage }) => {
  await worksheetPage.goto();
});

test("a successful query reports done @smoke", async ({ page, worksheetPage }) => {
  await worksheetPage.run("SELECT 1 AS ok");
  await expect(page.getByText(/done \d+/).first()).toBeVisible();
});

test("invalid SQL is rejected with a 422", async ({ page, worksheetPage }) => {
  const resp = page.waitForResponse(
    (r) => r.url().includes("/queries") && r.request().method() === "POST",
  );
  await worksheetPage.setSql("SELECT * FORM nope");
  await worksheetPage.runButton.click();
  expect((await resp).status()).toBe(422);
});

test("sandbox-escape statements (SET) are blocked by the guard @smoke", async ({
  page,
  worksheetPage,
}) => {
  const resp = page.waitForResponse(
    (r) => r.url().includes("/queries") && r.request().method() === "POST",
  );
  await worksheetPage.setSql("SET memory_limit='1GB'");
  await worksheetPage.runButton.click();
  expect((await resp).status()).toBe(422);
  const body = await (await resp).json();
  expect(JSON.stringify(body)).toContain("Disallowed statement");
  // The error renders as readable text, never "[object Object]".
  await expect(page.locator("body")).not.toContainText("[object Object]");
});

test("a running query can be cancelled", async ({ page, worksheetPage }) => {
  await worksheetPage.setSql("SELECT count(*) FROM range(500000000) t(i)");
  await worksheetPage.runButton.click();
  await page.locator("button", { hasText: "Cancel" }).click();
  await expect(page.getByRole("status").first()).toHaveText(/cancel/i, { timeout: 15_000 });
});
