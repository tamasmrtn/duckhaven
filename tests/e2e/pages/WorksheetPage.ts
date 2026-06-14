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

  get profileTab(): Locator {
    return this.page.getByRole("tab", { name: "profile" });
  }

  get resultsTab(): Locator {
    return this.page.getByRole("tab", { name: "results" });
  }

  async openProfile(): Promise<void> {
    await this.profileTab.click();
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
    await this.setSql(sql);
    // Arm the rows-response wait before running so the grid is read only after
    // the result page has actually been fetched (the control plane fetches the
    // first row window from the agent), not the instant "done" appears.
    const rowsFetched = this.page.waitForResponse(
      (r) => /\/queries\/[^/]+\/rows/.test(r.url()) && r.request().method() === "GET",
    );
    await this.runButton.click();
    await expect(this.page.getByText(/done \d+/).first()).toBeVisible({ timeout: 30_000 });
    await rowsFetched;
    await this.page.waitForTimeout(100); // let React commit the fetched rows
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

  /** Scroll the results grid to the bottom to trigger the next page fetch. */
  async scrollResultsToBottom(): Promise<void> {
    await this.page.evaluate(() => {
      const el = [...document.querySelectorAll(".overflow-auto")].find((c) =>
        c.querySelector("table"),
      );
      if (el) {
        el.scrollTop = el.scrollHeight;
        el.dispatchEvent(new Event("scroll", { bubbles: true }));
      }
    });
  }

  /** Scroll repeatedly until the grid has rendered `expected` rows (or stalls). */
  async loadRowsUntil(expected: number, maxScrolls = 30): Promise<number> {
    for (let i = 0; i < maxScrolls; i++) {
      if ((await this.rowCount()) >= expected) break;
      await this.scrollResultsToBottom();
      await this.page.waitForTimeout(250);
    }
    return this.rowCount();
  }
}
