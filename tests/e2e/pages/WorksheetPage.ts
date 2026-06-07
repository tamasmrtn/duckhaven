import { expect, type Locator, type Page } from "@playwright/test";

import { BASE_URL, WS_SLUG, setMonacoValue } from "../helpers";

export class WorksheetPage {
  constructor(private readonly page: Page) {}

  get runButton(): Locator {
    return this.page.locator('[aria-label="Run query (⌘↵)"]');
  }

  get newTabButton(): Locator {
    return this.page.locator('[aria-label="New worksheet"]');
  }

  async goto(ws = WS_SLUG): Promise<void> {
    await this.page.goto(`${BASE_URL}/${ws}/worksheets`);
  }

  async setSql(sql: string): Promise<void> {
    await setMonacoValue(this.page, sql);
  }

  async run(sql: string): Promise<void> {
    await this.setSql(sql);
    await this.runButton.click();
    await expect(this.page.getByText(/done \d+/).first()).toBeVisible({ timeout: 30_000 });
  }

  /** Run a query and return the rendered result grid as a matrix of cell text. */
  async runAndReadRows(sql: string): Promise<string[][]> {
    await this.run(sql);
    return this.page.evaluate(() =>
      [...document.querySelectorAll("table tbody tr")].map((tr) =>
        [...tr.querySelectorAll("td")].map((td) =>
          (td as HTMLElement).innerText.replace(/^Copy\s*/, "").trim(),
        ),
      ),
    );
  }

  async rowCount(): Promise<number> {
    return this.page.evaluate(
      () => document.querySelectorAll("table tbody tr").length,
    );
  }
}
