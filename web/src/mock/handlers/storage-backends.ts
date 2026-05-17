import { http, HttpResponse } from "msw";
import { STORAGE_BACKENDS } from "../fixtures/storage-backends";

export const storageBackendHandlers = [
  http.get("/api/admin/storage-backends", () => {
    return HttpResponse.json(STORAGE_BACKENDS);
  }),

  http.post("/api/admin/storage-backends", async ({ request }) => {
    const body = (await request.json()) as {
      kind: string;
      name: string;
      root_uri: string;
      uc_storage_credential_id?: string;
    };
    const backend = {
      id: `sb-${Date.now()}`,
      kind: body.kind,
      name: body.name,
      root_uri: body.root_uri,
      uc_storage_credential_id: body.uc_storage_credential_id ?? null,
      uc_credential_valid: body.uc_storage_credential_id ? true : null,
      workspace_count: 0,
      created_by: "Marton",
      created_at: new Date().toISOString(),
    };
    STORAGE_BACKENDS.push(backend as (typeof STORAGE_BACKENDS)[0]);
    return HttpResponse.json(backend, { status: 201 });
  }),

  http.delete("/api/admin/storage-backends/:id", ({ params }) => {
    const backend = STORAGE_BACKENDS.find((b) => b.id === params.id);
    if (!backend) return new HttpResponse(null, { status: 404 });
    if (backend.workspace_count > 0) {
      return HttpResponse.json(
        { error: "Backend is in use by one or more workspaces" },
        { status: 409 },
      );
    }
    return new HttpResponse(null, { status: 204 });
  }),
];
