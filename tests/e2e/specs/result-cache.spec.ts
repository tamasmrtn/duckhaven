/** The result cache from the worksheet: a repeated read is served without running. */
import { expect, test } from "../fixtures/test";
import { BASE_URL, DEFAULT_CATALOG, WS_SLUG } from "../helpers";

test("a repeated query is served from the cache and can be re-run without it", async ({
  page,
  worksheetPage,
}) => {
  const table = `${DEFAULT_CATALOG}.analytics.rc_e2e_${Date.now()}`;
  const sql = `SELECT count(*) AS n FROM ${table}`;
  await worksheetPage.goto();
  await worksheetPage.run(`CREATE TABLE ${table} AS SELECT i FROM range(10) r(i)`);

  try {
    await worksheetPage.run(sql);
    // Admission runs just after the first run finishes; the badge appears on the
    // first repeat that finds the entry.
    const cached = page.getByText(/^Cached/);
    await expect(async () => {
      await worksheetPage.run(sql);
      await expect(cached).toBeVisible({ timeout: 2_000 });
    }).toPass({ timeout: 30_000 });
    expect(await worksheetPage.rowCount()).toBe(1);

    const rerun = page.waitForRequest((r) => r.url().includes("/queries") && r.method() === "POST");
    await page.getByRole("button", { name: /re-run without cache/i }).click();
    expect((await rerun).postDataJSON()).toMatchObject({ use_cache: false });
    await expect(cached).toBeHidden({ timeout: 30_000 });

    await page.goto(`${BASE_URL}/${WS_SLUG}/history`);
    await expect(page.getByText("Result cache").first()).toBeVisible();
  } finally {
    await worksheetPage.goto();
    await worksheetPage.run(`DROP TABLE ${table}`);
  }
});
