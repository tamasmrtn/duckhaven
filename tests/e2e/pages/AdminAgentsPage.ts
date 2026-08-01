import { type Locator, type Page } from "@playwright/test";

import { BASE_URL, WS_SLUG } from "../helpers";

export class AdminAgentsPage {
  constructor(private readonly page: Page) {}

  async goto(ws = WS_SLUG): Promise<void> {
    await this.page.goto(`${BASE_URL}/${ws}/compute`);
  }

  get firstRow(): Locator {
    return this.page.locator("table tbody tr").first();
  }

  /** The health status dot (an icon with an accessible label like "healthy"). */
  get statusDot(): Locator {
    return this.page.locator('table tbody td [role="img"]').first();
  }
}
