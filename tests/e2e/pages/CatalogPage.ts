import { type Locator, type Page } from "@playwright/test";

import { BASE_URL, WS_SLUG } from "../helpers";

export class CatalogPage {
  constructor(private readonly page: Page) {}

  async goto(ws = WS_SLUG): Promise<void> {
    await this.page.goto(`${BASE_URL}/${ws}/catalog`);
  }

  async gotoTable(schema: string, table: string, ws = WS_SLUG): Promise<void> {
    await this.page.goto(`${BASE_URL}/${ws}/catalog/${schema}/${table}`);
  }

  async expandWorkspace(ws = WS_SLUG): Promise<void> {
    // The catalog tree auto-expands schema nodes, so only click to expand when
    // the node is currently collapsed (clicking an open node would collapse it).
    const node = this.page.getByRole("button", { name: ws });
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
