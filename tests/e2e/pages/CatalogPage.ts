import { type Locator, type Page } from "@playwright/test";

import { BASE_URL, DEFAULT_CATALOG, WS_SLUG } from "../helpers";

// A node's label button is named by its text plus any badges ("default",
// the storage icon), so match the name as a leading word.
function nodeName(text: string): RegExp {
  const escaped = text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`^${escaped}(\\s|$)`);
}

async function expandRow(row: Locator, chevron: string): Promise<void> {
  await row.waitFor();
  const toggle = row.getByRole("button", { name: chevron });
  if (await toggle.count()) await toggle.click();
}

export class CatalogPage {
  constructor(private readonly page: Page) {}

  async goto(ws = WS_SLUG): Promise<void> {
    await this.page.goto(`${BASE_URL}/${ws}/catalog`);
  }

  /** Open a table's detail view. The route is catalog-scoped:
   * `/$ws/catalog/$catalog/$schema/$table`. */
  async gotoTable(catalog: string, schema: string, table: string, ws = WS_SLUG): Promise<void> {
    await this.page.goto(`${BASE_URL}/${ws}/catalog/${catalog}/${schema}/${table}`);
  }

  /** Ensure a catalog node is expanded so its schemas are revealed. The tree
   * starts collapsed (and remembers what was opened), so click its chevron
   * only when it offers to expand. */
  async expandCatalog(catalog = DEFAULT_CATALOG): Promise<void> {
    await expandRow(this.catalogRow(catalog), "Expand catalog");
  }

  /** Ensure a schema node under `catalog` is expanded so its tables are
   * revealed. Scoped to the catalog: a schema may share its catalog's name. */
  async expandSchema(schema: string, catalog = DEFAULT_CATALOG): Promise<void> {
    await this.expandCatalog(catalog);
    const children = this.catalogRow(catalog).locator("xpath=following-sibling::div[1]");
    const row = children.getByRole("button", { name: nodeName(schema) }).first().locator("xpath=..");
    await expandRow(row, "Expand schema");
  }

  /** The row holding a catalog's chevron and label. */
  private catalogRow(catalog: string): Locator {
    return this.page
      .getByRole("button", { name: nodeName(catalog) })
      .first()
      .locator("xpath=..");
  }

  tableLink(name: string): Locator {
    // Tables in the shared catalog tree are buttons (click → open detail),
    // not anchors.
    return this.page.getByRole("button", { name });
  }
}
