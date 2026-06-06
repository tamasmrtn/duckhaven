import { setupWorker } from "msw/browser";
import { authHandlers } from "./handlers/auth";
import { setupHandlers } from "./handlers/setup";
import { workspaceHandlers } from "./handlers/workspaces";
import { agentHandlers } from "./handlers/agents";
import { metricsHandlers } from "./handlers/metrics";
import { schemaHandlers } from "./handlers/schemas";
import { queryHandlers } from "./handlers/queries";
import { storageBackendHandlers } from "./handlers/storage-backends";
import { userHandlers } from "./handlers/users";

export const worker = setupWorker(
  ...authHandlers,
  ...setupHandlers,
  ...workspaceHandlers,
  ...agentHandlers,
  ...metricsHandlers,
  ...schemaHandlers,
  ...queryHandlers,
  ...storageBackendHandlers,
  ...userHandlers,
);
