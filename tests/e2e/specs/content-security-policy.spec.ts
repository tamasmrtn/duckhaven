/**
 * The web app runs under its own Content-Security-Policy without tripping it.
 * The policy allows only DuckHaven's own origin, so a new third-party script,
 * font, worker or request in the app fails here before it fails for a user.
 */
import { expect, test } from "../fixtures/test";
import { BASE_URL, WS_SLUG, setMonacoValue } from "../helpers";

test("the app and its SQL editor load without a CSP violation", async ({ page }) => {
  const violations: string[] = [];
  page.on("console", (msg) => {
    if (msg.text().startsWith("CSP violation:")) violations.push(msg.text());
  });
  await page.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (e) => {
      (window as any).__cspViolated = true;
      console.log(`CSP violation: ${e.effectiveDirective} ${e.blockedURI}`);
    });
  });

  const response = await page.goto(`${BASE_URL}/${WS_SLUG}/worksheets`);
  expect(response?.headers()["content-security-policy"]).toContain("script-src 'self'");
  // The editor's language features run in a worker, the part of the build a
  // policy is most likely to break. Monaco starts it lazily after an edit, so
  // wait until it has either loaded or been refused.
  await setMonacoValue(page, "SELECT 1 AS one;");
  await page.waitForFunction(
    () =>
      (window as any).__cspViolated ||
      performance.getEntriesByType("resource").some((e) => e.name.includes("editor.worker")),
  );

  for (const path of ["catalog", "saved-queries", "history", "compute"]) {
    await page.goto(`${BASE_URL}/${WS_SLUG}/${path}`);
    await page.waitForLoadState("networkidle");
  }

  expect(violations).toEqual([]);
});
