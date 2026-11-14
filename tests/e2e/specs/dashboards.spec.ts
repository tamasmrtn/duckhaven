/**
 * A dashboard stored through the REST API, then viewed in the app: it opens
 * from the list, each widget's query runs as the viewer, and the answers
 * stream into the page without tripping the Content-Security-Policy.
 */
import { expect, test } from "../fixtures/test";
import { BASE_URL, DEFAULT_CATALOG, WS_SLUG } from "../helpers";

test("a published dashboard opens from the list and shows its widgets' data", async ({
  page,
  worksheetPage,
}) => {
  const stamp = Date.now();
  const table = `${DEFAULT_CATALOG}.analytics.dash_e2e_${stamp}`;
  const name = `e2e-${stamp}`;
  const violations: string[] = [];
  page.on("console", (msg) => {
    if (msg.text().startsWith("CSP violation:")) violations.push(msg.text());
  });
  await page.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (e) => {
      console.log(`CSP violation: ${e.effectiveDirective} ${e.blockedURI}`);
    });
  });

  await worksheetPage.goto();
  await worksheetPage.run(
    `CREATE TABLE ${table} AS SELECT i AS id, i * 10 AS amount FROM range(1, 6) r(i)`,
  );
  try {
    const spec = {
      version: 1,
      name,
      title: `E2E dashboard ${stamp}`,
      rows: [
        {
          widgets: [
            {
              id: "total",
              type: "kpi",
              title: "Total",
              sql: `SELECT sum(amount) AS total FROM ${table}`,
              columns: { value: "total" },
            },
            {
              id: "rows",
              type: "table",
              title: "Rows",
              sql: `SELECT id, amount FROM ${table} ORDER BY id`,
            },
            { id: "note", type: "text", markdown: "Made by the **e2e** suite." },
          ],
        },
      ],
    };
    const created = await page.request.post(`/api/workspaces/${WS_SLUG}/dashboards`, {
      data: { spec },
    });
    expect(created.status()).toBe(201);
    const published = await page.request.post(
      `/api/workspaces/${WS_SLUG}/dashboards/${name}/publish`,
      { data: { base_version: (await created.json()).version } },
    );
    expect(published.status()).toBe(200);

    await page.goto(`${BASE_URL}/${WS_SLUG}/dashboards`);
    await page.getByRole("link", { name: `E2E dashboard ${stamp}` }).click();

    const total = page.getByRole("region", { name: "Total" });
    await expect(total.getByTestId("kpi-value")).toHaveText("150", { timeout: 60_000 });
    const rows = page.getByRole("region", { name: "Rows" });
    await expect(rows.getByText("5 rows")).toBeVisible();
    await expect(page.getByText("Made by the")).toBeVisible();
    await expect(page.getByText(/answered in/)).toBeVisible();
    expect(violations).toEqual([]);
  } finally {
    await page.request.delete(`/api/workspaces/${WS_SLUG}/dashboards/${name}`);
    await worksheetPage.goto();
    await worksheetPage.run(`DROP TABLE ${table}`);
  }
});
