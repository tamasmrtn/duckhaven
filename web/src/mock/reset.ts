// Restores every mutable mock store + the deterministic seed to their initial
// state. Wired into the Vitest afterEach (tests/setup.ts) for test isolation.
//
// Intentionally NOT covered: the agents WebSocket (/agents/connect) and
// GET /healthz — neither is consumed by the browser SPA.
import { resetWorkspaces } from "./fixtures/workspaces";
import { resetCatalogs } from "./fixtures/catalogs";
import { resetSchemas } from "./fixtures/schemas";
import { resetQueries } from "./fixtures/queries";
import { resetSchedules } from "./fixtures/schedules";
import { resetStorageBackends } from "./fixtures/storage-backends";
import { resetCatalogMigrations } from "./fixtures/catalog-migrations";
import { resetAgents } from "./fixtures/agents";
import { resetMetrics } from "./fixtures/metrics";
import { resetMaintenance } from "./fixtures/maintenance";
import { resetLiveQueries } from "./handlers/queries";
import { resetSeed } from "./lib/seed";

export function resetMockState(): void {
  resetWorkspaces();
  resetCatalogs();
  resetSchemas();
  resetQueries();
  resetSchedules();
  resetStorageBackends();
  resetCatalogMigrations();
  resetAgents();
  resetMetrics();
  resetMaintenance();
  resetLiveQueries();
  resetSeed();
}
