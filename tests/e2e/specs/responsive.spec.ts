/**
 * Phone-width layouts. At 360px the catalog's 280px tree left its detail pane a
 * ~30px sliver, and the worksheet toolbar's Save buttons were clipped off the
 * right edge by an `overflow-hidden` ancestor, so neither could be reached.
 */
import { expect, test } from "../fixtures/test";
import { BASE_URL, DEFAULT_CATALOG, WS_SLUG } from "../helpers";

const PHONE = { width: 360, height: 800 };

test.use({ viewport: PHONE });

test("a catalog detail view gets the whole screen, with a way back to the tree", async ({
  page,
}) => {
  await page.goto(`${BASE_URL}/${WS_SLUG}/catalog/${DEFAULT_CATALOG}`);

  await expect(page.getByRole("button", { name: "Back to catalog tree" })).toBeVisible();

  // The detail pane is the page's only column, not a sliver beside the tree.
  const main = await page.locator("main").boundingBox();
  expect(main?.width ?? 0).toBeGreaterThan(PHONE.width - 80);

  await page.getByRole("button", { name: "Back to catalog tree" }).click();
  await expect(page).toHaveURL(new RegExp(`/${WS_SLUG}/catalog$`));
  await expect(page.getByPlaceholder("Search catalogs, schemas, tables…")).toBeVisible();
});

test("the worksheet's save actions stay on screen", async ({ page }) => {
  await page.goto(`${BASE_URL}/${WS_SLUG}/worksheets`);

  for (const name of ["Save as metric…", /^Save/]) {
    const button = page.getByRole("button", { name }).first();
    await expect(button).toBeVisible();
    const box = await button.boundingBox();
    expect(box, `no box for ${String(name)}`).not.toBeNull();
    expect(box!.x + box!.width).toBeLessThanOrEqual(PHONE.width);
  }
});
