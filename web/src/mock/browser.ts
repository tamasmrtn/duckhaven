import { setupWorker } from "msw/browser";
import { authHandlers } from "./handlers/auth";
import { setupHandlers } from "./handlers/setup";
import { workspaceHandlers } from "./handlers/workspaces";
import { catalogHandlers } from "./handlers/catalogs";
import { agentHandlers } from "./handlers/agents";
import { metricsHandlers } from "./handlers/metrics";
import { schemaHandlers } from "./handlers/schemas";
import { queryHandlers } from "./handlers/queries";
import { scheduleHandlers } from "./handlers/schedules";
import { assistantHandlers } from "./handlers/assistant";
import { storageBackendHandlers } from "./handlers/storage-backends";
import { catalogMigrationHandlers } from "./handlers/catalog-migrations";
import { userHandlers } from "./handlers/users";
import { serviceAccountHandlers } from "./handlers/service-accounts";
import { grantHandlers } from "./handlers/grants";
import { maintenanceHandlers } from "./handlers/maintenance";

export const worker = setupWorker(
  ...authHandlers,
  ...setupHandlers,
  ...workspaceHandlers,
  ...catalogHandlers,
  ...agentHandlers,
  ...metricsHandlers,
  ...schemaHandlers,
  ...queryHandlers,
  ...scheduleHandlers,
  ...assistantHandlers,
  ...storageBackendHandlers,
  ...catalogMigrationHandlers,
  ...userHandlers,
  ...serviceAccountHandlers,
  ...grantHandlers,
  ...maintenanceHandlers,
);
