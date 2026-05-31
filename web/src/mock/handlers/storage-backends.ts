import { http, HttpResponse } from "msw";
import { STORAGE_BACKENDS } from "../fixtures/storage-backends";
import { CURRENT_USER } from "../fixtures/users";
import { nextId } from "../lib/seed";
import { httpError } from "../lib/errors";
import type { BackendKind } from "@/types/storage-backend";

export const storageBackendHandlers = [
  http.get("/api/admin/storage-backends", () => {
    return HttpResponse.json(STORAGE_BACKENDS);
  }),

  http.post("/api/admin/storage-backends", async ({ request }) => {
    const body = (await request.json()) as {
      kind: BackendKind;
      name: string;
      root_uri: string;
      uc_storage_credential_id?: string;
    };
    const backend = {
      id: nextId("sb"),
      kind: body.kind,
      name: body.name,
      root_uri: body.root_uri,
      uc_storage_credential_id: body.uc_storage_credential_id ?? null,
      uc_credential_valid: body.uc_storage_credential_id ? true : null,
      workspace_count: 0,
      created_by: CURRENT_USER.id,
      created_at: new Date().toISOString(),
    };
    STORAGE_BACKENDS.push(backend);
    return HttpResponse.json(backend, { status: 201 });
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
