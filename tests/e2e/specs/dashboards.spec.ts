/**
 * A dashboard stored through the REST API, then viewed in the app: it opens
 * from the list, each widget's query runs as the viewer, and the answers
 * stream into the page, charts included, without tripping the
 * Content-Security-Policy. A dashboard is also made and published in the
 * editor.
 */
import { expect, test } from "../fixtures/test";
import { BASE_URL, DEFAULT_CATALOG, setMonacoValue, WS_SLUG } from "../helpers";

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
            {
              id: "by_id",
              type: "bar",
              title: "Amount by id",
              sql: `SELECT CAST(id AS VARCHAR) AS id, amount FROM ${table} ORDER BY id`,
              columns: { x: "id", y: ["amount"] },
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

    // A chart is a picture with a text alternative, and its rows a menu away.
    const bars = page.getByRole("region", { name: "Amount by id" });
    await expect(bars.getByRole("img", { name: /A bar chart of amount by id/ })).toBeVisible();
    await bars.getByRole("button", { name: "Actions for Amount by id" }).click();
    await page.getByRole("menuitem", { name: "View as table" }).click();
    await expect(bars.getByText("5 rows")).toBeVisible();
    expect(violations).toEqual([]);
  } finally {
    await page.request.delete(`/api/workspaces/${WS_SLUG}/dashboards/${name}`);
    await worksheetPage.goto();
    await worksheetPage.run(`DROP TABLE ${table}`);
  }
});

test("a click on a bar filters the other widgets, and a second click restores them", async ({
  page,
  worksheetPage,
}) => {
  const stamp = Date.now();
  const table = `dash_xf_${stamp}`;
  const model = `xf_${stamp}`;
  const name = `xf-${stamp}`;
  const api = `/api/workspaces/${WS_SLUG}`;
  const ok = async (resp: Promise<{ status(): number; text(): Promise<string> }>) => {
    const r = await resp;
    expect(r.status(), await r.text()).toBeLessThan(300);
  };

  await worksheetPage.goto();
  await worksheetPage.run(
    `CREATE TABLE ${DEFAULT_CATALOG}.analytics.${table} AS SELECT i AS id, i * 10 AS amount, ` +
      `CASE WHEN i % 2 = 0 THEN 'even' ELSE 'odd' END AS parity FROM range(1, 6) r(i)`,
  );
  try {
    // A model over the table: the bars break down by parity, the KPI sums amount.
    const base = `${api}/semantic/models/${model}`;
    await ok(page.request.post(`${api}/semantic/models`, { data: { slug: model, name: "XF" } }));
    await ok(
      page.request.post(`${base}/datasets`, {
        data: {
          name: "rows",
          catalog: DEFAULT_CATALOG,
          schema_name: "analytics",
          table_name: table,
          primary_key: ["id"],
        },
      }),
    );
    await ok(page.request.post(`${base}/dimensions`, { data: { name: "parity", dataset: "rows" } }));
    await ok(
      page.request.post(`${base}/metrics`, {
        data: { name: "amount", dataset: "rows", agg: "sum", expr: "amount" },
      }),
    );
    await ok(page.request.post(`${base}/publish`));
    await ok(page.request.patch(`${base}/metrics/amount`, { data: { status: "published" } }));

    const spec = {
      version: 1,
      name,
      title: `XF dashboard ${stamp}`,
      parameters: { parity: { type: "dimension", model, dimension: "parity" } },
      rows: [
        {
          widgets: [
            {
              id: "total",
              type: "kpi",
              title: "Total amount",
              query: { model, metrics: ["amount"] },
            },
            {
              id: "by_parity",
              type: "bar",
              title: "Amount by parity",
              query: { model, metrics: ["amount"], dimensions: ["parity"] },
            },
          ],
        },
      ],
    };
    const created = await page.request.post(`${api}/dashboards`, { data: { spec } });
    expect(created.status(), await created.text()).toBe(201);
    await ok(
      page.request.post(`${api}/dashboards/${name}/publish`, {
        data: { base_version: (await created.json()).version },
      }),
    );

    await page.goto(`${BASE_URL}/${WS_SLUG}/dashboards/${name}`);
    const total = page.getByRole("region", { name: "Total amount" }).getByTestId("kpi-value");
    await expect(total).toHaveText("150", { timeout: 60_000 });

    // Bars rank largest first: odd (1 + 3 + 5) then even (2 + 4).
    const bars = page.getByRole("region", { name: "Amount by parity" });
    const even = bars.locator(".recharts-bar-rectangle").nth(1);
    await even.click();
    await expect(page).toHaveURL(/p\.parity=even/);
    await expect(total).toHaveText("60", { timeout: 60_000 });
    // The chart keeps both bars and says which is chosen.
    await expect(bars.getByRole("img", { name: /even is selected/ })).toBeVisible();
    await expect(bars.locator(".recharts-bar-rectangle")).toHaveCount(2);

    await even.click();
    await expect(page).not.toHaveURL(/p\.parity/);
    await expect(total).toHaveText("150", { timeout: 60_000 });
  } finally {
    await page.request.delete(`${api}/dashboards/${name}`);
    await page.request.delete(`${api}/semantic/models/${model}`);
    await worksheetPage.goto();
    await worksheetPage.run(`DROP TABLE ${DEFAULT_CATALOG}.analytics.${table}`);
  }
});

test("a dashboard made in the editor previews each change, then publishes", async ({ page }) => {
  const stamp = Date.now();
  const title = `Editor e2e ${stamp}`;
  const name = `editor-e2e-${stamp}`;
  const violations: string[] = [];
  page.on("console", (msg) => {
    if (msg.text().startsWith("CSP violation:")) violations.push(msg.text());
  });
  await page.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (e) => {
      console.log(`CSP violation: ${e.effectiveDirective} ${e.blockedURI}`);
    });
  });

  await page.goto(`${BASE_URL}/${WS_SLUG}/dashboards`);
  await page.getByRole("button", { name: "New dashboard" }).first().click();
  const create = page.getByRole("dialog", { name: "New dashboard" });
  await create.getByLabel("Title").fill(title);
  await create.getByLabel("Name").fill(name);
  await create.getByRole("button", { name: "Create" }).click();
  try {
    await expect(page).toHaveURL(new RegExp(`/dashboards/${name}/edit`));
    await expect(page.getByText("Preview, viewing as you")).toBeVisible();
    // The starter file previews once the editor is ready for typing.
    await expect(page.getByRole("region", { name: "about" })).toBeVisible();

    // The file is edited through Monaco, whose YAML worker must load under the
    // CSP. The rows are in flow style, because Monaco indents each new line.
    await setMonacoValue(
      page,
      [
        "version: 1",
        `name: ${name}`,
        `title: ${title}`,
        "rows: [{widgets: [{id: answer, type: kpi, title: The answer, " +
          "sql: SELECT 42 AS answer, columns: {value: answer}}]}]",
      ].join("\n"),
    );
    const kpi = page.getByRole("region", { name: "The answer" }).getByTestId("kpi-value");
    await expect(kpi).toHaveText("42", { timeout: 60_000 });
    await expect(page.getByText("No problems: it can be published as it stands.")).toBeVisible();

    await page.getByRole("button", { name: "Save draft" }).click();
    await expect(page.getByText(/^Draft saved .+ from the app$/)).toBeVisible();

    await page.getByRole("button", { name: "Publish" }).click();
    const dialog = page.getByRole("dialog", { name: "Publish dashboard" });
    await dialog.getByLabel("What changed (optional)").fill("First version");
    await dialog.getByRole("button", { name: "Publish" }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByText("No changes since it was published")).toBeVisible();

    await page.getByRole("button", { name: "Revisions" }).click();
    await expect(page.getByText("First version")).toBeVisible();
    await expect(page.getByRole("button", { name: "Open revision 1 in the editor" })).toBeVisible();
    await page.keyboard.press("Escape");

    await page.getByRole("link", { name: "Done" }).click();
    await expect(page).toHaveURL(new RegExp(`/dashboards/${name}$`));
    await expect(kpi).toHaveText("42", { timeout: 60_000 });
    expect(violations).toEqual([]);
  } finally {
    await page.request.delete(`/api/workspaces/${WS_SLUG}/dashboards/${name}`);
  }
});
