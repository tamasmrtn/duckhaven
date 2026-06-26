import { http, HttpResponse } from "msw";
import { STORAGE_BACKENDS } from "../fixtures/storage-backends";
import { CURRENT_USER } from "../fixtures/users";
import { nextId } from "../lib/seed";
import { httpError } from "../lib/errors";
import type {
  BackendKind,
  StorageBackendConfig,
} from "@/types/storage-backend";

export const storageBackendHandlers = [
  http.get("/api/admin/storage-backends", () => {
    return HttpResponse.json(STORAGE_BACKENDS);
  }),

  http.post("/api/admin/storage-backends", async ({ request }) => {
    const body = (await request.json()) as {
      kind: BackendKind;
      name: string;
      root_uri: string;
      config?: StorageBackendConfig;
    };
    const backend = {
      id: nextId("sb"),
      kind: body.kind,
      name: body.name,
      root_uri: body.root_uri,
      config: body.config ?? null,
      workspace_count: 0,
      created_by: CURRENT_USER.id,
      created_at: new Date().toISOString(),
    };
    STORAGE_BACKENDS.push(backend);
    return HttpResponse.json(backend, { status: 201 });
  }),

  http.post("/api/admin/storage-backends/:id/health", ({ params }) => {
    const backend = STORAGE_BACKENDS.find((b) => b.id === params.id);
    if (!backend) return httpError(404, "Storage backend not found");
    if (backend.kind === "object_store") {
      return HttpResponse.json({
        valid: true,
        detail: "Bundled object store; no external credentials to validate.",
      });
    }
    return HttpResponse.json({
      valid: true,
      detail:
        "Vended credentials reached storage (1 object(s) under the probe path).",
    });
  }),

  http.delete("/api/admin/storage-backends/:id", ({ params }) => {
    const backend = STORAGE_BACKENDS.find((b) => b.id === params.id);
    if (!backend) return httpError(404, "Storage backend not found");
    if (backend.workspace_count > 0) {
      return httpError(409, "Backend is in use by one or more workspaces");
    }
    return new HttpResponse(null, { status: 204 });
  }),
];
