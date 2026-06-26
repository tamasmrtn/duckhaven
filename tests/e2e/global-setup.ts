/**
 * Make the live stack self-bootstrapping so the suite runs against a fresh
 * `make compose-up`: ensure a first admin exists (consuming DH_SETUP_TOKEN when
 * the stack still needs one), that the analytics workspace exists, and that it
 * has a default catalog (workspaces now start empty — catalogs are decoupled).
 * All via the real API — the UI setup flow itself is asserted in bootstrap.spec.ts.
 */
import { request, type FullConfig } from "@playwright/test";

import { ADMIN_EMAIL, ADMIN_PASSWORD, BASE_URL, WS_SLUG } from "./helpers";

const SETUP_TOKEN = process.env.DH_SETUP_TOKEN ?? "";

export default async function globalSetup(_config: FullConfig): Promise<void> {
  const ctx = await request.newContext({ baseURL: BASE_URL });
  try {
    const status = await (await ctx.get("/api/setup/status")).json();
    if (status.needs_admin) {
      if (!SETUP_TOKEN) {
        throw new Error(
          "Stack needs a first admin but DH_SETUP_TOKEN is not set. Provide it via:\n" +
            '  export DH_SETUP_TOKEN="$(docker compose -f deploy/docker-compose.yml ' +
            'exec -T api cat /var/duckhaven/setup_token)"',
        );
      }
      const created = await ctx.post("/api/setup/admin", {
        headers: { "X-Setup-Token": SETUP_TOKEN },
        data: { email: ADMIN_EMAIL, password: ADMIN_PASSWORD, name: "E2E Admin" },
      });
      if (!created.ok()) {
        throw new Error(`setup/admin failed: ${created.status()} ${await created.text()}`);
      }
    }

    const login = await ctx.post("/api/auth/login", {
      data: { email: ADMIN_EMAIL, password: ADMIN_PASSWORD },
    });
    if (!login.ok()) {
      throw new Error(`login failed: ${login.status()} ${await login.text()}`);
    }

    const workspaces = await (await ctx.get("/api/workspaces")).json();
    if (!workspaces.some((w: { slug: string }) => w.slug === WS_SLUG)) {
      const created = await ctx.post("/api/workspaces", {
        data: { slug: WS_SLUG, name: "Analytics" },
      });
      if (!created.ok() && created.status() !== 409) {
        throw new Error(`workspace create failed: ${created.status()} ${await created.text()}`);
      }
    }

    // Workspaces start with no catalog now; ensure the analytics workspace has a
    // default one (on bundled object storage) so worksheet queries can run.
    const catalogs = await (await ctx.get(`/api/workspaces/${WS_SLUG}/catalogs`)).json();
    if (Array.isArray(catalogs) && catalogs.length === 0) {
      const created = await ctx.post(`/api/workspaces/${WS_SLUG}/catalogs`, {
        data: { name: "analytics" },
      });
      if (!created.ok() && created.status() !== 409) {
        throw new Error(`catalog create failed: ${created.status()} ${await created.text()}`);
      }
    }
  } finally {
    await ctx.dispose();
  }
}
