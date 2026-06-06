/** Admin → Agents: visibility, health, and advertised capabilities. */
import { expect, test } from "../fixtures/test";

test("the bundled agent is listed as healthy with storage extensions @smoke", async ({
  adminAgentsPage,
}) => {
  await adminAgentsPage.goto();

  await expect(adminAgentsPage.firstRow).toBeVisible();
  // Health is shown as a status dot with an accessible label.
  await expect(adminAgentsPage.statusDot).toHaveAttribute("aria-label", "healthy");
  // Storage extensions must be advertised, else every dispatch is rejected.
  await expect(adminAgentsPage.firstRow).toContainText("httpfs");
  await expect(adminAgentsPage.firstRow).toContainText("iceberg");
});
