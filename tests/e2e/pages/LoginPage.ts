import { expect, type Locator, type Page } from "@playwright/test";

import { ADMIN_EMAIL, ADMIN_PASSWORD, BASE_URL, WS_SLUG } from "../helpers";

export class LoginPage {
  constructor(private readonly page: Page) {}

  async goto(): Promise<void> {
    await this.page.goto(`${BASE_URL}/login`);
  }

  async login(email = ADMIN_EMAIL, password = ADMIN_PASSWORD): Promise<void> {
    await this.goto();
    await this.page.getByRole("textbox", { name: "Email" }).fill(email);
    await this.page.getByRole("textbox", { name: "Password" }).fill(password);
    // Exact match so a configured OIDC provider's "Sign in with <IdP>" button
    // does not also match the local sign-in button.
    await this.page.getByRole("button", { name: "Sign in", exact: true }).click();
  }

  async loginAsAdmin(): Promise<void> {
    await this.login();
    await expect(this.page).toHaveURL(new RegExp(`/${WS_SLUG}/`));
  }

  get error(): Locator {
    return this.page.locator('[role="alert"]').first();
  }
}
