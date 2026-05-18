import { setupWorker } from "msw/browser";
import { authHandlers } from "./handlers/auth";
import { workspaceHandlers } from "./handlers/workspaces";
import { agentHandlers } from "./handlers/agents";
import { schemaHandlers } from "./handlers/schemas";
import { queryHandlers } from "./handlers/queries";
import { storageBackendHandlers } from "./handlers/storage-backends";

export const worker = setupWorker(
  ...authHandlers,
  ...workspaceHandlers,
  ...agentHandlers,
  ...schemaHandlers,
  ...queryHandlers,
  ...storageBackendHandlers,
);
