/**
 * The three access layers (issue #129), end to end through the full stack:
 *
 *   1. Global role    — `admin` vs `user`: who can administer the platform.
 *   2. Workspace role — membership gates a workspace's data.
 *   3. Data grants    — a *scoped* catalog narrows access per catalog/schema/table,
 *                       with a discovery-only `metadata` tier, capping the
 *                       workspace role (grants only narrow, never widen).
 *
 * A fresh, isolated workspace + scoped catalog + member are built via the API,
 * then each layer is asserted from both the admin's and the member's point of
 * view (a second, separately-authenticated browser session).
 */
import {
  type APIRequestContext,
  type Browser,
  type Page,
  request,
} from "@playwright/test";

import { expect, test } from "../fixtures/test";
import {
  ADMIN_EMAIL,
  ADMIN_PASSWORD,
  ADMIN_STORAGE_STATE,
  BASE_URL,
} from "../helpers";
import { CatalogPage } from "../pages/CatalogPage";
import { LoginPage } from "../pages/LoginPage";
import { WorksheetPage } from "../pages/WorksheetPage";

const TS = Date.now();
const WS = `acl-${TS}`; // the member's workspace
const WS_OTHER = `acl-other-${TS}`; // a workspace the member is NOT in
const CAT_NAME = `acl_${TS}`; // identifier-safe catalog name
const MEMBER_EMAIL = `acl-analyst-${TS}@test.local`;
const MEMBER_PW = "TestPassword123";

let admin: APIRequestContext;
let catSlug: string;
let memberId: string;
let memberCtx: Awaited<ReturnType<Browser["newContext"]>>;
let memberPage: Page;

test.describe.configure({ mode: "serial" });

async function apiLogin(
  email: string,
  password: string,
): Promise<APIRequestContext> {
  const ctx = await request.newContext({ baseURL: BASE_URL });
  const r = await ctx.post("/api/auth/login", { data: { email, password } });
  expect(r.ok(), `login ${email}: ${r.status()}`).toBeTruthy();
  return ctx;
}

test.beforeAll(async ({ browser }) => {
  admin = await apiLogin(ADMIN_EMAIL, ADMIN_PASSWORD);

  // Two isolated workspaces; the member joins only the first.
  for (const slug of [WS, WS_OTHER]) {
    const r = await admin.post("/api/workspaces", {
      data: { slug, name: slug },
    });
    expect(r.ok(), `create ${slug}`).toBeTruthy();
  }
  const catResp = await admin.post(`/api/workspaces/${WS}/catalogs`, {
    data: { name: CAT_NAME },
  });
  expect(catResp.ok(), await catResp.text()).toBeTruthy();
  catSlug = (await catResp.json()).slug;

  // The member: a plain global `user`, workspace `writer` (so a grant can be
  // shown to narrow him below his workspace role).
  const userResp = await admin.post("/api/admin/users", {
    data: {
      email: MEMBER_EMAIL,
      name: "ACL Analyst",
      password: MEMBER_PW,
      role: "user",
    },
  });
  expect(userResp.ok(), await userResp.text()).toBeTruthy();
  memberId = (await userResp.json()).id;
  const memberResp = await admin.post(`/api/workspaces/${WS}/members`, {
    data: { user_id: memberId, role: "writer" },
  });
  expect(memberResp.ok(), await memberResp.text()).toBeTruthy();

  // Seed data while the catalog is still OPEN (the owner has full access). Two
  // schemas let us test schema-level hiding. Done through the admin worksheet
  // so real DDL/DML runs on the agent.
  const adminBrowserCtx = await browser.newContext({
    storageState: ADMIN_STORAGE_STATE,
  });
  const adminWs = new WorksheetPage(await adminBrowserCtx.newPage());
  await adminWs.goto(WS);
  for (const sql of [
    "CREATE SCHEMA sales",
    "CREATE TABLE sales.orders (id INTEGER, amount DECIMAL(10,2))",
    "INSERT INTO sales.orders VALUES (1, 9.99), (2, 20.00)",
    "CREATE TABLE sales.metrics (id INTEGER)",
    "INSERT INTO sales.metrics VALUES (1)",
    "CREATE SCHEMA secret",
    "CREATE TABLE secret.ledger (id INTEGER)",
    "INSERT INTO secret.ledger VALUES (1)",
  ]) {
    await adminWs.run(sql);
  }
  await adminBrowserCtx.close();

  // Switch the catalog to scoped and grant the member narrow access:
  //   reader   on sales.orders    → can query
  //   metadata on sales.metrics   → can describe, not read
  //   (nothing on secret.*        → hidden)
  const mode = await admin.patch(
    `/api/workspaces/${WS}/catalogs/${catSlug}/access-mode`,
    {
      data: { access_mode: "scoped" },
    },
  );
  expect(mode.ok(), await mode.text()).toBeTruthy();
  const grant = (data: object) =>
    admin.put(`/api/workspaces/${WS}/catalogs/${catSlug}/grants`, { data });
  expect(
    (
      await grant({
        user_id: memberId,
        schema_name: "sales",
        table_name: "orders",
        tier: "reader",
      })
    ).ok(),
  ).toBeTruthy();
  expect(
    (
      await grant({
        user_id: memberId,
        schema_name: "sales",
        table_name: "metrics",
        tier: "metadata",
      })
    ).ok(),
  ).toBeTruthy();

  // A persistent, separately-authenticated session for the member.
  memberCtx = await browser.newContext();
  memberPage = await memberCtx.newPage();
  await new LoginPage(memberPage).login(MEMBER_EMAIL, MEMBER_PW);
  await expect(memberPage).toHaveURL(new RegExp(`/${WS}/`));
});

test.afterAll(async () => {
  await memberCtx?.close();
  await admin?.dispose();
});

// ── Layer 1: global role ──────────────────────────────────────────────────

test("layer 1 — a non-admin cannot reach the admin area", async () => {
  // Client-side gate: the admin shell never renders for a plain user.
  await memberPage.goto(`${BASE_URL}/${WS}/admin/agents`);
  await expect(memberPage.getByText("Admin access required")).toBeVisible();
  await expect(memberPage.getByRole("button", { name: "Agents" })).toHaveCount(
    0,
  );
  // ...and the Admin nav entry is absent for them.
  await expect(
    memberPage.getByRole("button", { name: "Admin", exact: true }),
  ).toHaveCount(0);

  // Server-side: the admin API is 403 for the member.
  const member = await apiLogin(MEMBER_EMAIL, MEMBER_PW);
  expect((await member.get("/api/admin/users")).status()).toBe(403);
  await member.dispose();
});

// ── Layer 2: workspace role (membership) ──────────────────────────────────

test("layer 2 — workspace membership gates data access", async () => {
  const member = await apiLogin(MEMBER_EMAIL, MEMBER_PW);
  // A member of WS can list its catalogs.
  expect((await member.get(`/api/workspaces/${WS}/catalogs`)).status()).toBe(
    200,
  );
  // A workspace the member is not in is forbidden.
  expect(
    (await member.get(`/api/workspaces/${WS_OTHER}/catalogs`)).status(),
  ).toBe(403);
  await member.dispose();
});

// ── Layer 3: data grants (scoped catalog) ─────────────────────────────────

test("layer 3 — the scoped tree is filtered to granted objects", async () => {
  const catalog = new CatalogPage(memberPage);
  await catalog.goto(WS);
  await catalog.expandCatalog(catSlug);
  // The granted schema is visible; the ungranted one is hidden entirely.
  await expect(memberPage.getByRole("button", { name: "sales" })).toBeVisible();
  await expect(memberPage.getByRole("button", { name: "secret" })).toHaveCount(
    0,
  );
});

test("layer 3 — a reader grant allows querying the table", async () => {
  const ws = new WorksheetPage(memberPage);
  await ws.goto(WS);
  const rows = await ws.runAndReadRows(
    `SELECT id FROM ${catSlug}.sales.orders ORDER BY id`,
  );
  expect(rows).toEqual([["1"], ["2"]]);
});

test("layer 3 — a query on an ungranted table is rejected pre-dispatch", async () => {
  const ws = new WorksheetPage(memberPage);
  await ws.goto(WS);
  await ws.setSql(`SELECT * FROM ${catSlug}.secret.ledger`);
  await ws.runButton.click();
  await expect(memberPage.getByText("Query failed")).toBeVisible();
  await expect(
    memberPage.getByText(/Not authorized \(reader\) on .*secret\.ledger/),
  ).toBeVisible();
});

test("layer 3 — a metadata grant describes but cannot read rows", async () => {
  // Querying the rows is denied...
  const ws = new WorksheetPage(memberPage);
  await ws.goto(WS);
  await ws.setSql(`SELECT * FROM ${catSlug}.sales.metrics`);
  await ws.runButton.click();
  await expect(memberPage.getByText("Query failed")).toBeVisible();
  await expect(
    memberPage.getByText(/Not authorized \(reader\) on .*sales\.metrics/),
  ).toBeVisible();

  // ...but the table detail loads and the sample tab explains the block.
  await new CatalogPage(memberPage).gotoTable(catSlug, "sales", "metrics", WS);
  await expect(memberPage.getByText(/previewing rows requires/i)).toBeVisible();
});

test("layer 3 — a join is rejected if any referenced table lacks reader", async () => {
  const ws = new WorksheetPage(memberPage);
  await ws.goto(WS);
  await ws.setSql(
    `SELECT o.id FROM ${catSlug}.sales.orders o ` +
      `JOIN ${catSlug}.secret.ledger l ON o.id = l.id`,
  );
  await ws.runButton.click();
  await expect(memberPage.getByText("Query failed")).toBeVisible();
  await expect(
    memberPage.getByText(/Not authorized \(reader\) on .*secret\.ledger/),
  ).toBeVisible();
});

test("layer 3 — a grant caps the workspace role (writer cannot write a reader table)", async () => {
  // The member is a workspace `writer`, but only holds `reader` on sales.orders,
  // so the write is denied — the grant narrows the workspace role.
  const ws = new WorksheetPage(memberPage);
  await ws.goto(WS);
  await ws.setSql(`INSERT INTO ${catSlug}.sales.orders VALUES (3, 1.00)`);
  await ws.runButton.click();
  await expect(memberPage.getByText("Query failed")).toBeVisible();
  await expect(
    memberPage.getByText(/Not authorized \(writer\) on .*sales\.orders/),
  ).toBeVisible();
});
