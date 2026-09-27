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
  // It runs the default runtime, built as that runtime's image, so the control
  // plane recognises it rather than inferring it from the DuckDB version.
  await expect(adminAgentsPage.firstRow).toContainText("DuckDB 1.5 · v1.5.");
});
