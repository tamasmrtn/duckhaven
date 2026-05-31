// Restores every mutable mock store + the deterministic seed to their initial
// state. Wired into the Vitest afterEach (tests/setup.ts) for test isolation.
//
// Intentionally NOT covered: the agents WebSocket (/agents/connect) and
// GET /healthz — neither is consumed by the browser SPA.
import { resetWorkspaces } from "./fixtures/workspaces";
import { resetSchemas } from "./fixtures/schemas";
import { resetQueries } from "./fixtures/queries";
import { resetStorageBackends } from "./fixtures/storage-backends";
import { resetAgents } from "./fixtures/agents";
import { resetLiveQueries } from "./handlers/queries";
import { resetSeed } from "./lib/seed";

export function resetMockState(): void {
  resetWorkspaces();
  resetSchemas();
  resetQueries();
  resetStorageBackends();
  resetAgents();
  resetLiveQueries();
  resetSeed();
}
