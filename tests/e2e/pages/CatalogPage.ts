import { type Locator, type Page } from "@playwright/test";

import { BASE_URL, DEFAULT_CATALOG, WS_SLUG } from "../helpers";

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

  /** Ensure the catalog node is expanded so its schemas/tables are revealed.
   * The default catalog auto-expands, so this only clicks when collapsed. */
  async expandCatalog(catalog = DEFAULT_CATALOG): Promise<void> {
    const node = this.page.getByRole("button", { name: catalog }).first();
    if ((await node.getAttribute("aria-expanded")) === "false") {
      await node.click();
    }
  }

  tableLink(name: string): Locator {
    // Tables in the shared catalog tree are buttons (click → open detail),
    // not anchors.
    return this.page.getByRole("button", { name });
  }
}
