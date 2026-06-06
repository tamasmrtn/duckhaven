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
    await this.page.getByRole("button", { name: ws }).click();
  }

  tableLink(name: string): Locator {
    return this.page.getByRole("link", { name });
  }
}
