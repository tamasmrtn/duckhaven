/**
 * Many-to-many workspaces ↔ catalogs, end to end through the full stack.
 *
 * Catalogs are decoupled, first-class entities: a workspace attaches one or
 * more, and the same catalog can be attached to several workspaces. These specs
 * provision workspaces/catalogs over the real API, then exercise the actual
 * agent attach + DuckDB execution path through the worksheet UI:
 *   1. multiple catalogs in one workspace → a cross-catalog join,
 *   2. a catalog shared across two workspaces → data written in A read from B,
 *   3. drop refused while attached, allowed after detaching everywhere,
 *   4. unqualified names resolve against the workspace's default catalog.
 */
import { type APIRequestContext } from "@playwright/test";

import { expect, test } from "../fixtures/test";

// Unique per run so reruns against a persistent stack don't collide on the
// globally-unique workspace slug / catalog name.
const SFX = Date.now()
  .toString(36)
  .replace(/[^a-z0-9]/g, "");

async function createWorkspace(request: APIRequestContext, slug: string): Promise<void> {
  const r = await request.post("/api/workspaces", { data: { slug, name: slug } });
  expect(r.ok() || r.status() === 409, `create workspace ${slug}`).toBeTruthy();
}

async function createCatalog(
  request: APIRequestContext,
  ws: string,
  name: string,
): Promise<string> {
  const r = await request.post(`/api/workspaces/${ws}/catalogs`, { data: { name } });
  expect(r.ok(), `create catalog ${name}: ${await r.text()}`).toBeTruthy();
  return (await r.json()).id;
}

test("multiple catalogs in one workspace support a cross-catalog join @smoke", async ({
  page,
  worksheetPage,
}) => {
  const ws = `mc-${SFX}`;
  const raw = `raw_${SFX}`;
  const curated = `curated_${SFX}`;

  await createWorkspace(page.request, ws);
  await createCatalog(page.request, ws, raw);
  await createCatalog(page.request, ws, curated);

  await worksheetPage.goto(ws);
  // Each catalog has a default `analytics` namespace; address tables fully
  // qualified (catalog.schema.table). Every bound catalog is attached per query.
  await worksheetPage.run(`CREATE TABLE "${raw}"."analytics"."events" (id BIGINT, label VARCHAR)`);
  await worksheetPage.run(`INSERT INTO "${raw}"."analytics"."events" VALUES (1,'a'),(2,'b')`);
  await worksheetPage.run(
    `CREATE TABLE "${curated}"."analytics"."users" (id BIGINT, name VARCHAR)`,
  );
  await worksheetPage.run(
    `INSERT INTO "${curated}"."analytics"."users" VALUES (1,'Alice'),(2,'Bob')`,
  );

  const rows = await worksheetPage.runAndReadRows(
    `SELECT e.id, e.label, u.name ` +
      `FROM "${raw}"."analytics"."events" e ` +
      `JOIN "${curated}"."analytics"."users" u ON e.id = u.id ` +
      `ORDER BY e.id`,
  );
  expect(rows).toEqual([
    ["1", "a", "Alice"],
    ["2", "b", "Bob"],
  ]);
});

test("a catalog attached to two workspaces is shared (M:N)", async ({ page, worksheetPage }) => {
  const wsA = `share-a-${SFX}`;
  const wsB = `share-b-${SFX}`;
  const shared = `shared_${SFX}`;

  // Create the catalog in A and write a row through A's worksheet.
  await createWorkspace(page.request, wsA);
  const catalogId = await createCatalog(page.request, wsA, shared);
  await worksheetPage.goto(wsA);
  await worksheetPage.run(`CREATE TABLE "${shared}"."analytics"."t" (id BIGINT)`);
  await worksheetPage.run(`INSERT INTO "${shared}"."analytics"."t" VALUES (7)`);

  // Attach the same catalog to a second workspace.
  await createWorkspace(page.request, wsB);
  const attach = await page.request.post(`/api/workspaces/${wsB}/catalogs/attach`, {
    data: { catalog_id: catalogId },
  });
  expect(attach.ok(), `attach: ${await attach.text()}`).toBeTruthy();
  expect((await attach.json()).attached_workspaces).toBe(2);

  // The data written via A is visible from B — it is the same catalog.
  await worksheetPage.goto(wsB);
  const rows = await worksheetPage.runAndReadRows(`SELECT id FROM "${shared}"."analytics"."t"`);
  expect(rows).toEqual([["7"]]);
});

test("dropping a catalog is refused while attached, allowed after detaching everywhere", async ({
  page,
}) => {
  const wsA = `drop-a-${SFX}`;
  const wsB = `drop-b-${SFX}`;
  const cat = `dropme_${SFX}`;

  await createWorkspace(page.request, wsA);
  const catalogId = await createCatalog(page.request, wsA, cat);
  await createWorkspace(page.request, wsB);
  const attach = await page.request.post(`/api/workspaces/${wsB}/catalogs/attach`, {
    data: { catalog_id: catalogId },
  });
  expect(attach.ok()).toBeTruthy();

  // Attached to two workspaces → drop is refused.
  const blocked = await page.request.delete(`/api/catalogs/${catalogId}`);
  expect(blocked.status()).toBe(409);

  // Detach from both workspaces, then the drop succeeds.
  expect((await page.request.delete(`/api/workspaces/${wsA}/catalogs/${cat}`)).status()).toBe(204);
  expect((await page.request.delete(`/api/workspaces/${wsB}/catalogs/${cat}`)).status()).toBe(204);
  expect((await page.request.delete(`/api/catalogs/${catalogId}`)).status()).toBe(204);

  // It is gone from the global list.
  const all = (await (await page.request.get("/api/catalogs")).json()) as { id: string }[];
  expect(all.some((c) => c.id === catalogId)).toBeFalsy();
});

test("unqualified names resolve against the workspace's default catalog", async ({
  page,
  worksheetPage,
}) => {
  const ws = `dfl-${SFX}`;
  const a = `cat_a_${SFX}`; // first attached → default
  const b = `cat_b_${SFX}`;

  await createWorkspace(page.request, ws);
  await createCatalog(page.request, ws, a); // becomes the default catalog
  await createCatalog(page.request, ws, b);

  await worksheetPage.goto(ws);
  // Unqualified CREATE/INSERT/SELECT land in the active (default) catalog `a`.
  await worksheetPage.run(`CREATE TABLE "analytics"."dfl" (id BIGINT)`);
  await worksheetPage.run(`INSERT INTO "analytics"."dfl" VALUES (99)`);

  // The same unqualified name resolves to the default catalog `a`, not `b`.
  const viaDefault = await worksheetPage.runAndReadRows(`SELECT id FROM "analytics"."dfl"`);
  expect(viaDefault).toEqual([["99"]]);
  const viaQualified = await worksheetPage.runAndReadRows(
    `SELECT id FROM "${a}"."analytics"."dfl"`,
  );
  expect(viaQualified).toEqual([["99"]]);
});
